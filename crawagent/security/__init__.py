"""安全检查模块（P5）：vibe coding 安全扫描 + 自动修复

模块组成：
- models.py            : 漏洞/扫描任务/补丁 Pydantic 模型 + SQLite 存储
- network_capture.py   : 浏览器网络请求全捕获（XSS/SQLi/SSRF 注入测试）
- vuln_scanner.py       : OWASP Top 10 扫描引擎
- auto_fixer.py         : 生成 diff + 补丁文件
- security_hook.py     : harness 的 security hook（拦截危险操作 + 异常检测）
- security_lane.py     : harness 的 security lane 编排

设计原则：
- 扫描器是无副作用的只读检查（不实际注入恶意 payload 到数据库）
- 注入测试用受控 payload + 回显检测，不破坏目标
- 扫描结果结构化（漏洞类型/位置/证据/修复建议），便于 LLM 自主决策
- 修复建议由 vuln_scanner 生成，auto_fixer 转为 diff 文件
"""
from crawagent.security.models import (
    Vulnerability,
    ScanTask,
    ScanResult,
    Severity,
    VulnCategory,
    SecurityStore,
    get_security_store,
)
from crawagent.security.network_capture import NetworkCapture
from crawagent.security.vuln_scanner import VulnScanner
from crawagent.security.auto_fixer import AutoFixer, FixPatch
from crawagent.security.security_hook import SecurityHookHandler
from crawagent.security.security_lane import SecurityLane

__all__ = [
    "Vulnerability",
    "ScanTask",
    "ScanResult",
    "Severity",
    "VulnCategory",
    "SecurityStore",
    "get_security_store",
    "NetworkCapture",
    "VulnScanner",
    "AutoFixer",
    "FixPatch",
    "SecurityHookHandler",
    "SecurityLane",
]
