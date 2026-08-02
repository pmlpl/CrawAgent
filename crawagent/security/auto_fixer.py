"""自动修复器（P5-5）：生成 diff + 补丁文件

输入：Vulnerability 列表
输出：FixPatch（unified diff 格式 + 修复说明文件）

设计：
- 不是全自动应用补丁（危险），而是生成 .patch 文件让用户审阅后 apply
- 补丁按文件聚合（同文件的多个漏洞合并为一个补丁）
- 支持常见配置文件修复（nginx.conf / .htaccess / Server header）
- 代码漏洞（SQLi/XSS）生成修复示例代码片段，不自动 patch 源码
"""
from __future__ import annotations

import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from loguru import logger

from crawagent.security.models import Vulnerability, Severity, VulnCategory


@dataclass
class FixPatch:
    """单个修复补丁"""
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    scan_task_id: str = ""
    title: str = ""                       # 补丁标题
    target_file: str = ""                 # 目标文件（如 nginx.conf / .htaccess）
    vulnerability_ids: List[str] = field(default_factory=list)  # 关联的漏洞 ID
    diff: str = ""                        # unified diff 格式
    description: str = ""                 # 修复说明
    severity: Severity = Severity.MEDIUM
    created_at: float = field(default_factory=time.time)

    def to_summary(self) -> str:
        return f"[{self.severity.value}] {self.title} ({len(self.vulnerability_ids)} 个漏洞)"


class AutoFixer:
    """自动修复器

    用法：
        fixer = AutoFixer()
        patches = await fixer.generate_patches(vulnerabilities)
        for patch in patches:
            print(patch.diff)
            # 用户审阅后：git apply patch.diff
    """

    # 等级白名单：只有这些等级的漏洞会自动生成补丁
    # 高危/严重默认只报告，不自动 patch（避免误操作，需人工审阅）
    DEFAULT_ALLOWED_SEVERITIES = (Severity.LOW, Severity.MEDIUM)

    async def generate_patches(
        self,
        vulnerabilities: List[Vulnerability],
        scan_task_id: str = "",
        allowed_severities: Optional[List[Severity]] = None,
    ) -> tuple:
        """根据漏洞列表生成修复补丁（按严重性白名单过滤）

        Args:
            vulnerabilities: 漏洞列表
            scan_task_id: 扫描任务 ID
            allowed_severities: 允许自动生成补丁的等级白名单；
                不在白名单内的漏洞只报告（reported_only），不生成补丁。

        Returns:
            (patches: List[FixPatch], reported_only: List[Vulnerability])
        """
        allowed = set(allowed_severities or self.DEFAULT_ALLOWED_SEVERITIES)

        # 按等级分流：白名单内 → 自动补丁；白名单外（高危/严重）→ 仅报告
        auto_vulns: List[Vulnerability] = []
        reported_only: List[Vulnerability] = []
        for v in vulnerabilities:
            (auto_vulns if v.severity in allowed else reported_only).append(v)

        patches: List[FixPatch] = []

        # 按类别分组生成补丁（仅白名单内漏洞）
        by_category: Dict[VulnCategory, List[Vulnerability]] = {}
        for v in auto_vulns:
            by_category.setdefault(v.category, []).append(v)

        for category, vulns in by_category.items():
            patch = await self._fix_category(category, vulns, scan_task_id)
            if patch:
                patches.append(patch)

        logger.info(
            f"[AutoFixer] 生成 {len(patches)} 个补丁（覆盖 {len(auto_vulns)} 个白名单内漏洞），"
            f"{len(reported_only)} 个高危/严重漏洞仅报告"
        )
        return patches, reported_only

    async def _fix_category(
        self,
        category: VulnCategory,
        vulns: List[Vulnerability],
        scan_task_id: str,
    ) -> Optional[FixPatch]:
        """按漏洞类别生成补丁"""
        # 取最高严重性
        max_severity = max(v.severity for v in vulns)

        if category == VulnCategory.A05_SECURITY_MISCONFIG:
            return self._fix_security_headers(vulns, scan_task_id, max_severity)
        elif category == VulnCategory.CLICKJACKING:
            return self._fix_clickjacking(vulns, scan_task_id, max_severity)
        elif category == VulnCategory.A03_INJECTION:
            return self._fix_sqli(vulns, scan_task_id, max_severity)
        elif category == VulnCategory.XSS:
            return self._fix_xss(vulns, scan_task_id, max_severity)
        elif category == VulnCategory.INFO_DISCLOSURE:
            return self._fix_info_disclosure(vulns, scan_task_id, max_severity)
        elif category == VulnCategory.A02_CRYPTO_FAILURES:
            return self._fix_crypto(vulns, scan_task_id, max_severity)
        else:
            # 其他类别生成通用建议
            return self._fix_generic(category, vulns, scan_task_id, max_severity)

    # ---- 具体修复策略 ----

    def _fix_security_headers(
        self, vulns: List[Vulnerability], task_id: str, sev: Severity
    ) -> FixPatch:
        """安全头缺失 → 生成 nginx.conf 补丁"""
        missing = set()
        for v in vulns:
            if "strict-transport-security" in v.evidence.lower():
                missing.add("Strict-Transport-Security")
            if "content-security-policy" in v.evidence.lower():
                missing.add("Content-Security-Policy")
            if "x-frame-options" in v.evidence.lower():
                missing.add("X-Frame-Options")
            if "x-content-type-options" in v.evidence.lower():
                missing.add("X-Content-Type-Options")
            if "referrer-policy" in v.evidence.lower():
                missing.add("Referrer-Policy")

        lines = []
        if "Strict-Transport-Security" in missing:
            lines.append("add_header Strict-Transport-Security \"max-age=31536000; includeSubDomains\" always;")
        if "Content-Security-Policy" in missing:
            lines.append("add_header Content-Security-Policy \"default-src 'self'; script-src 'self'\" always;")
        if "X-Frame-Options" in missing:
            lines.append("add_header X-Frame-Options \"DENY\" always;")
        if "X-Content-Type-Options" in missing:
            lines.append("add_header X-Content-Type-Options \"nosniff\" always;")
        if "Referrer-Policy" in missing:
            lines.append("add_header Referrer-Policy \"strict-origin-when-cross-origin\" always;")

        patch_content = "\n".join(lines)
        diff = self._make_diff(
            "nginx.conf",
            original="# 安全头配置（在 server 块内添加）",
            patched=patch_content,
        )
        return FixPatch(
            scan_task_id=task_id,
            title=f"添加 {len(missing)} 个安全响应头",
            target_file="nginx.conf",
            vulnerability_ids=[v.id for v in vulns],
            diff=diff,
            description=f"在 nginx.conf 的 server 块内添加缺失的安全响应头：{', '.join(missing)}",
            severity=sev,
        )

    def _fix_clickjacking(
        self, vulns: List[Vulnerability], task_id: str, sev: Severity
    ) -> FixPatch:
        diff = self._make_diff(
            "nginx.conf",
            original="# 点击劫持防护",
            patched='add_header X-Frame-Options "DENY" always;',
        )
        return FixPatch(
            scan_task_id=task_id,
            title="添加 X-Frame-Options 防止点击劫持",
            target_file="nginx.conf",
            vulnerability_ids=[v.id for v in vulns],
            diff=diff,
            description="设置 X-Frame-Options: DENY 禁止页面被 iframe 嵌入",
            severity=sev,
        )

    def _fix_sqli(
        self, vulns: List[Vulnerability], task_id: str, sev: Severity
    ) -> FixPatch:
        example = (
            "# 修复前（有漏洞）：\n"
            'cursor.execute(f"SELECT * FROM users WHERE id={user_id}")\n\n'
            "# 修复后（参数化查询）：\n"
            'cursor.execute("SELECT * FROM users WHERE id=%s", (user_id,))\n\n'
            "# ORM 推荐写法：\n"
            "user = User.objects.filter(id=user_id).first()"
        )
        diff = self._make_diff(
            "app/db.py",
            original='# cursor.execute(f"SELECT * FROM users WHERE id={user_id}")',
            patched='cursor.execute("SELECT * FROM users WHERE id=%s", (user_id,))',
        )
        return FixPatch(
            scan_task_id=task_id,
            title=f"SQL 注入修复（{len(vulns)} 处）",
            target_file="app/db.py",
            vulnerability_ids=[v.id for v in vulns],
            diff=diff,
            description=(
                "将字符串拼接的 SQL 改为参数化查询（PreparedStatement）。\n\n"
                f"修复示例：\n{example}\n\n"
                f"受影响参数：{', '.join(set(v.parameter for v in vulns if v.parameter))}"
            ),
            severity=sev,
        )

    def _fix_xss(
        self, vulns: List[Vulnerability], task_id: str, sev: Severity
    ) -> FixPatch:
        affected_params = ", ".join(set(v.parameter for v in vulns if v.parameter))
        diff = self._make_diff(
            "app/templates.py",
            original="return render_template('page.html', content=user_input)",
            patched="from markupsafe import escape\nreturn render_template('page.html', content=escape(user_input))",
        )
        return FixPatch(
            scan_task_id=task_id,
            title=f"XSS 修复（{len(vulns)} 处）",
            target_file="app/templates.py",
            vulnerability_ids=[v.id for v in vulns],
            diff=diff,
            description=(
                "对用户输入做 HTML 实体编码，避免 XSS 攻击。\n\n"
                "修复方案：\n"
                "1. 模板引擎自动转义（Jinja2 默认开启）\n"
                "2. 手动转义：from markupsafe import escape\n"
                "3. 配置 CSP 头限制脚本来源\n\n"
                f"受影响参数：{affected_params}"
            ),
            severity=sev,
        )

    def _fix_info_disclosure(
        self, vulns: List[Vulnerability], task_id: str, sev: Severity
    ) -> FixPatch:
        lines = []
        for v in vulns:
            if "Server header" in v.evidence:
                lines.append('# 隐藏 Server 头：server_tokens off;')
            if "X-Powered-By" in v.evidence:
                lines.append('# 移除 X-Powered-By：fastcgi_hide_header X-Powered-By;')
        diff = self._make_diff(
            "nginx.conf",
            original="# 隐藏服务器版本信息",
            patched="\n".join(lines) if lines else "# 无需修改",
        )
        return FixPatch(
            scan_task_id=task_id,
            title=f"信息泄露修复（{len(vulns)} 处）",
            target_file="nginx.conf",
            vulnerability_ids=[v.id for v in vulns],
            diff=diff,
            description="隐藏服务器版本号和技术栈信息，避免攻击者获取指纹。",
            severity=sev,
        )

    def _fix_crypto(
        self, vulns: List[Vulnerability], task_id: str, sev: Severity
    ) -> FixPatch:
        diff = self._make_diff(
            "nginx.conf",
            original="# 混合内容修复",
            patched=(
                "# 强制 HTTP 跳转 HTTPS\n"
                "server {\n"
                "    listen 80;\n"
                "    server_name example.com;\n"
                "    return 301 https://$server_name$request_uri;\n"
                "}"
            ),
        )
        return FixPatch(
            scan_task_id=task_id,
            title="混合内容修复",
            target_file="nginx.conf",
            vulnerability_ids=[v.id for v in vulns],
            diff=diff,
            description="将 HTTP 请求 301 重定向到 HTTPS，避免混合内容。",
            severity=sev,
        )

    def _fix_generic(
        self, category: VulnCategory, vulns: List[Vulnerability], task_id: str, sev: Severity
    ) -> FixPatch:
        """通用修复建议（无具体 diff）"""
        return FixPatch(
            scan_task_id=task_id,
            title=f"{category.value} 修复建议（{len(vulns)} 处）",
            target_file="(需人工审查)",
            vulnerability_ids=[v.id for v in vulns],
            diff="# 此类别漏洞需人工审查，无自动补丁\n",
            description=(
                f"漏洞类别 {category.value} 需人工分析后修复。\n\n"
                f"涉及漏洞：\n" + "\n".join(f"- {v.to_summary()}" for v in vulns)
            ),
            severity=sev,
        )

    # ---- 工具 ----

    def _make_diff(self, filename: str, original: str, patched: str) -> str:
        """生成 unified diff 格式"""
        from difflib import unified_diff
        orig_lines = original.splitlines(keepends=True)
        patch_lines = patched.splitlines(keepends=True)
        diff = unified_diff(
            orig_lines,
            patch_lines,
            fromfile=f"a/{filename}",
            tofile=f"b/{filename}",
        )
        return "".join(diff)

    async def save_patches(
        self, patches: List[FixPatch], output_dir: str = "./output/security_patches"
    ) -> List[str]:
        """保存补丁到文件，返回文件路径列表"""
        os.makedirs(output_dir, exist_ok=True)
        paths = []
        for patch in patches:
            filename = f"{patch.id}.patch"
            path = os.path.join(output_dir, filename)
            content = (
                f"# {patch.title}\n"
                f"# 严重性: {patch.severity.value}\n"
                f"# 目标文件: {patch.target_file}\n"
                f"# 关联漏洞: {', '.join(patch.vulnerability_ids)}\n"
                f"# 生成时间: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(patch.created_at))}\n"
                f"#\n"
                f"# 修复说明:\n"
                f"# {patch.description.replace(chr(10), chr(10) + '# ')}\n"
                f"#\n"
                f"{patch.diff}\n"
            )
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            paths.append(path)
            logger.info(f"[AutoFixer] 补丁已保存: {path}")
        return paths
