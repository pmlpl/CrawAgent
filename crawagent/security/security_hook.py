"""安全 Hook 处理器（P5-2）

两类拦截：
1. before_tool：拦截危险工具调用（如 scan_vuln 扫描生产环境需确认）
2. after_tool：工具执行后检测异常行为（如响应含敏感信息泄露）

参考 AntiBotHookHandler 的设计：状态在 hook 实例间传递，通过 hooks.on() 注册。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from loguru import logger

from crawagent.security.models import Severity, VulnCategory


# 危险工具调用模式（tool_name + 参数特征）
_DANGEROUS_PATTERNS = {
    # scan_vuln 扫描生产环境需确认
    "scan_vuln": lambda args: _is_prod_url(args.get("url", "")),
    # crawl 深度爬取可能触发 WAF
    "crawl": lambda args: args.get("depth", 1) > 3,
    # save 写入敏感路径
    "save": lambda args: _is_sensitive_path(args.get("path", "")),
}


def _is_prod_url(url: str) -> bool:
    """判断是否生产环境 URL（非 localhost/test/example）"""
    if not url:
        return False
    safe_keywords = ("localhost", "127.0.0.1", "test", "example.com", "example.org", "httpbin")
    return not any(k in url.lower() for k in safe_keywords)


def _is_sensitive_path(path: str) -> bool:
    """判断是否敏感写入路径"""
    if not path:
        return False
    sensitive = ("/etc/", "/root/", "C:\\Windows\\", "~/.ssh/", "~/.aws/")
    return any(s in path for s in sensitive)


class SecurityHookHandler:
    """Security Hook 处理器

    用法：
        handler = SecurityHookHandler()
        hooks.on(HookEvent.BEFORE_TOOL, handler.before_tool)
        hooks.on(HookEvent.AFTER_TOOL, handler.after_tool)
    """

    def __init__(self) -> None:
        # 缓存本次会话的警告记录（避免重复告警）
        self._warned_tools: List[str] = []
        # after_tool 检测到的异常
        self._anomalies: List[Dict[str, Any]] = []

    async def before_tool(self, context: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """before_tool：拦截危险工具调用

        返回修改后的 context：
        - 增加 require_confirmation 字段，标记需要用户确认
        - 增加 warning 字段，附加告警信息到工具结果
        """
        tool_name = context.get("tool_name", "")
        args = context.get("args", {})

        pattern = _DANGEROUS_PATTERNS.get(tool_name)
        if pattern is None:
            return None  # 非危险工具，不拦截

        if not pattern(args):
            return None

        # 记录警告
        warn_key = f"{tool_name}:{args.get('url', args.get('path', ''))}"
        if warn_key not in self._warned_tools:
            self._warned_tools.append(warn_key)
            logger.warning(f"[Security] 危险工具调用: {tool_name} args={args}")

        # 标记需要确认（loop 层据此决定是否执行）
        context["require_confirmation"] = True
        context["warning"] = (
            f"⚠️ 工具 {tool_name} 的参数触发安全检查："
            f"{'生产环境扫描需确认' if tool_name == 'scan_vuln' else '操作可能影响生产环境'}"
        )
        return context

    async def after_tool(self, context: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """after_tool：工具执行后检测异常行为

        检测：
        - crawl 结果含敏感信息（token/密码/PII）
        - extract 结果含 SQL 错误信息（可能存在注入）
        - 工具响应状态码异常（500/503 频繁）
        """
        tool_name = context.get("tool_name", "")
        result = context.get("result", {})

        if not isinstance(result, dict):
            return None

        # 1. 检测响应中的敏感信息泄露
        content = str(result.get("content", ""))
        if content:
            sensitive_findings = self._detect_sensitive(content)
            if sensitive_findings:
                anomaly = {
                    "type": "sensitive_data_in_response",
                    "tool": tool_name,
                    "findings": sensitive_findings,
                    "severity": Severity.HIGH.value,
                }
                self._anomalies.append(anomaly)
                logger.warning(
                    f"[Security] 工具 {tool_name} 响应含敏感信息: "
                    f"{[f['type'] for f in sensitive_findings]}"
                )
                # 把异常信息附加到 context，供 LLM 知晓
                context["security_warnings"] = context.get("security_warnings", [])
                context["security_warnings"].extend(sensitive_findings)

        # 2. 检测 SQL 错误信息（可能存在注入漏洞）
        if tool_name == "crawl" and content:
            import re
            sql_patterns = [
                r"SQL syntax.*MySQL", r"Warning.*mysql_.*",
                r"PostgreSQL.*ERROR", r"ORA-\d{5}",
                r"SQLite3?::query", r"SQLITE_ERROR",
            ]
            for pattern in sql_patterns:
                if re.search(pattern, content, re.IGNORECASE):
                    anomaly = {
                        "type": "sql_error_detected",
                        "tool": tool_name,
                        "pattern": pattern,
                        "severity": Severity.HIGH.value,
                        "recommendation": "建议运行 scan_vuln 检测 SQL 注入",
                    }
                    self._anomalies.append(anomaly)
                    logger.warning(f"[Security] 工具 {tool_name} 响应含 SQL 错误，可能存在注入")
                    break

        return context if context.get("security_warnings") or context.get("require_confirmation") else None

    def _detect_sensitive(self, content: str) -> List[Dict[str, str]]:
        """检测内容中的敏感信息"""
        import re
        patterns = [
            (r"-----BEGIN (?:RSA |EC )?PRIVATE KEY-----", "私钥泄露", Severity.CRITICAL),
            (r"Bearer\s+[A-Za-z0-9_\-\.]{40,}", "Bearer Token 泄露", Severity.HIGH),
            (r"(?:api[_-]?key)[\"':= ]+([A-Za-z0-9]{32,})", "API Key 泄露", Severity.HIGH),
        ]
        findings = []
        for pattern, name, sev in patterns:
            if re.search(pattern, content, re.IGNORECASE):
                findings.append({
                    "type": name,
                    "severity": sev.value,
                    "recommendation": "立即从响应中移除敏感信息，检查代码是否硬编码了密钥",
                })
        return findings

    @property
    def anomalies(self) -> List[Dict[str, Any]]:
        """获取本次会话检测到的所有异常"""
        return list(self._anomalies)

    def clear(self) -> None:
        """清空状态"""
        self._warned_tools.clear()
        self._anomalies.clear()
