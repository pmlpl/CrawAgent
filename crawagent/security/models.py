"""安全扫描数据模型 + SQLite 存储（P5）

- Vulnerability: 单个漏洞记录（类型/位置/证据/严重性/修复建议）
- ScanTask: 扫描任务配置（目标 URL/扫描类别/深度）
- ScanResult: 扫描结果汇总
- SecurityStore: SQLite 持久化（与 MonitorStore 风格一致）
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger
from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# 枚举
# ---------------------------------------------------------------------------

class Severity(str, Enum):
    """漏洞严重性等级（CVSS 简化版）"""
    INFO = "info"          # 信息泄露（版本号/路径）
    LOW = "low"            # 低危（Cookie 未 HttpOnly）
    MEDIUM = "medium"      # 中危（CSRF/点击劫持）
    HIGH = "high"          # 高危（SQL 注入/存储型 XSS）
    CRITICAL = "critical"  # 严重（RCE/认证绕过）


class VulnCategory(str, Enum):
    """OWASP Top 10 漏洞类别（2022 版）"""
    A01_BROKEN_ACCESS_CONTROL = "A01_Broken_Access_Control"
    A02_CRYPTO_FAILURES = "A02_Cryptographic_Failures"
    A03_INJECTION = "A03_Injection"                  # SQL/NoSQL/Command/LDAP
    A04_INSECURE_DESIGN = "A04_Insecure_Design"
    A05_SECURITY_MISCONFIG = "A05_Security_Misconfiguration"
    A06_VULN_COMPONENTS = "A06_Vulnerable_Components"
    A07_AUTH_FAILURES = "A07_Identification_Auth_Failures"
    A08_DATA_INTEGRITY = "A08_Software_Data_Integrity_Failures"
    A09_LOGGING_FAILURES = "A09_Security_Logging_Failures"
    A10_SSRF = "A10_SSRF"
    # 额外常见 Web 漏洞（非 OWASP Top 10 但常扫）
    XSS = "XSS"                                       # 跨站脚本（A03 子类，单列便于规则）
    CSRF = "CSRF"
    CLICKJACKING = "Clickjacking"
    INFO_DISCLOSURE = "Information_Disclosure"


class ScanStatus(str, Enum):
    """扫描任务状态"""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


# ---------------------------------------------------------------------------
# Pydantic 模型
# ---------------------------------------------------------------------------

class Vulnerability(BaseModel):
    """单个漏洞记录"""
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    scan_task_id: str = ""                              # 所属扫描任务
    category: VulnCategory = VulnCategory.INFO_DISCLOSURE
    severity: Severity = Severity.INFO
    title: str = ""                                     # 漏洞标题
    url: str = ""                                       # 发现漏洞的 URL
    parameter: str = ""                                 # 受影响参数（如 id/q）
    method: str = "GET"                                 # HTTP 方法
    evidence: str = ""                                  # 证据（响应片段/截图描述）
    payload: str = ""                                   # 测试 payload（如 ' OR 1=1--）
    request_url: str = ""                               # 完整测试请求 URL
    remediation: str = ""                               # 修复建议
    cwe_id: str = ""                                    # CWE 编号（如 CWE-79）
    confidence: float = 0.0                             # 置信度 0-1
    created_at: float = Field(default_factory=time.time)

    def to_summary(self) -> str:
        """生成简洁摘要（用于 LLM/通知）"""
        sev = self.severity.value.upper()
        cat = self.category.value
        loc = f" param={self.parameter}" if self.parameter else ""
        return f"[{sev}] {cat}: {self.title}{loc} (置信度 {self.confidence:.0%})"


class ScanTask(BaseModel):
    """扫描任务配置"""
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    name: str = ""
    url: str                                            # 目标 URL
    categories: List[VulnCategory] = Field(default_factory=list)  # 空则扫全部
    depth: int = 1                                      # 爬取深度（发现更多可测页面）
    headers: Dict[str, str] = Field(default_factory=dict)  # 自定义请求头（如 Cookie）
    timeout: int = 30
    enabled: bool = True
    status: ScanStatus = ScanStatus.PENDING
    started_at: float = 0.0
    finished_at: float = 0.0
    vuln_count: int = 0                                 # 漏洞总数（缓存，便于列表展示）
    created_at: float = Field(default_factory=time.time)
    # 内部字段
    last_alert_at: float = 0.0                          # 最近一次告警时间（去抖用，预留）
    consecutive_failures: int = 0


class ScanResult(BaseModel):
    """扫描结果汇总"""
    task: ScanTask
    vulnerabilities: List[Vulnerability] = Field(default_factory=list)
    pages_scanned: int = 0
    requests_sent: int = 0
    duration_seconds: float = 0.0
    error: str = ""

    @property
    def severity_counts(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for v in self.vulnerabilities:
            counts[v.severity.value] = counts.get(v.severity.value, 0) + 1
        return counts


# ---------------------------------------------------------------------------
# SQLite 存储
# ---------------------------------------------------------------------------

_DB_PATH = os.path.expanduser("~/.crawagent/security.db")


class SecurityStore:
    """SQLite 持久化（扫描任务 + 漏洞记录）

    与 MonitorStore 风格一致：JSON 序列化字段 + 简单 CRUD。
    """

    def __init__(self, db_path: str = _DB_PATH) -> None:
        self.db_path = os.path.expanduser(db_path)
        os.makedirs(os.path.dirname(self.db_path) or ".", exist_ok=True)
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._conn() as c:
            c.execute("""
                CREATE TABLE IF NOT EXISTS scan_tasks (
                    id TEXT PRIMARY KEY,
                    data TEXT NOT NULL,
                    created_at REAL
                )
            """)
            c.execute("""
                CREATE TABLE IF NOT EXISTS vulnerabilities (
                    id TEXT PRIMARY KEY,
                    scan_task_id TEXT NOT NULL,
                    data TEXT NOT NULL,
                    created_at REAL,
                    FOREIGN KEY (scan_task_id) REFERENCES scan_tasks(id) ON DELETE CASCADE
                )
            """)
            c.execute("""
                CREATE INDEX IF NOT EXISTS idx_vulns_task ON vulnerabilities(scan_task_id)
            """)

    # ---- 扫描任务 CRUD ----

    def create_task(self, task: ScanTask) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO scan_tasks (id, data, created_at) VALUES (?, ?, ?)",
                (task.id, task.model_dump_json(), task.created_at),
            )

    def get_task(self, task_id: str) -> Optional[ScanTask]:
        with self._conn() as c:
            row = c.execute("SELECT data FROM scan_tasks WHERE id = ?", (task_id,)).fetchone()
        if not row:
            return None
        return ScanTask.model_validate_json(row["data"])

    def update_task(self, task: ScanTask) -> None:
        task.vuln_count = self.count_vulns(task.id)
        self.create_task(task)

    def delete_task(self, task_id: str) -> None:
        with self._conn() as c:
            c.execute("DELETE FROM vulnerabilities WHERE scan_task_id = ?", (task_id,))
            c.execute("DELETE FROM scan_tasks WHERE id = ?", (task_id,))

    def list_tasks(self, limit: int = 100) -> List[ScanTask]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT data FROM scan_tasks ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [ScanTask.model_validate_json(r["data"]) for r in rows]

    # ---- 漏洞记录 CRUD ----

    def add_vulnerability(self, vuln: Vulnerability) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO vulnerabilities (id, scan_task_id, data, created_at) VALUES (?, ?, ?, ?)",
                (vuln.id, vuln.scan_task_id, vuln.model_dump_json(), vuln.created_at),
            )

    def list_vulnerabilities(
        self, task_id: str = "", severity: str = "", limit: int = 200
    ) -> List[Vulnerability]:
        query = "SELECT data FROM vulnerabilities"
        conditions = []
        params: List[Any] = []
        if task_id:
            conditions.append("scan_task_id = ?")
            params.append(task_id)
        # severity 在 data JSON 里，用 Python 过滤更可靠
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        with self._conn() as c:
            rows = c.execute(query, params).fetchall()
        vulns = [Vulnerability.model_validate_json(r["data"]) for r in rows]
        if severity:
            vulns = [v for v in vulns if v.severity.value == severity]
        return vulns

    def count_vulns(self, task_id: str) -> int:
        with self._conn() as c:
            row = c.execute(
                "SELECT COUNT(*) AS n FROM vulnerabilities WHERE scan_task_id = ?",
                (task_id,),
            ).fetchone()
        return row["n"] if row else 0


_store_instance: Optional[SecurityStore] = None


def get_security_store() -> SecurityStore:
    """全局单例（线程安全由 SQLite 连接池保证，每调用每连接）"""
    global _store_instance
    if _store_instance is None:
        _store_instance = SecurityStore()
    return _store_instance
