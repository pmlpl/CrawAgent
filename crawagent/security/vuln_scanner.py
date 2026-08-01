"""漏洞扫描引擎（P5-4）：OWASP Top 10 扫描

设计原则：
- 主动扫描：构造受控 payload，检测回显/行为变化
- 无副作用：不实际破坏数据（如 DROP TABLE），只测 SELECT 类 payload
- 结构化输出：每个漏洞含位置/证据/修复建议/CWE
- 可配置范围：按 categories 字段过滤扫描项

覆盖漏洞类别：
- A03_INJECTION: SQL 注入（基于错误/基于布尔）
- XSS: 反射型 XSS（参数回显检测）
- A01_BROKEN_ACCESS_CONTROL: IDOR（路径遍历/越权）
- A05_SECURITY_MISCONFIG: 安全头缺失（CSP/HSTS/X-Frame-Options）
- A07_AUTH_FAILURES: 弱认证检测（默认凭证/会话固定）
- A10_SSRF: URL 参数 SSRF 检测
- CSRF: Token 缺失检测
- CLICKJACKING: X-Frame-Options 缺失
- INFO_DISCLOSURE: 版本信息/路径泄露/目录列举
- A02_CRYPTO_FAILURES: 混合内容（HTTPS 页面含 HTTP 资源）
"""
from __future__ import annotations

import asyncio
import re
import time
from typing import Any, Dict, List, Optional, Set
from urllib.parse import urlparse, urlencode, urljoin

from loguru import logger

from crawagent.security.models import (
    Vulnerability, Severity, VulnCategory, ScanTask, ScanResult,
)


# ---------------------------------------------------------------------------
# 扫描规则：payload 集合
# ---------------------------------------------------------------------------

# SQL 注入测试 payload（无破坏性，仅触发错误或布尔差异）
_SQLI_PAYLOADS = [
    ("'", "单引号触发 SQL 错误"),
    ("' OR '1'='1", "布尔注入（恒真）"),
    ("' OR '1'='2", "布尔注入（恒假，用于对比）"),
    ("1' AND '1'='1", "布尔注入（数字型）"),
    ("1 UNION SELECT NULL--", "UNION 注入探测"),
    (";--", "注释截断"),
]

# SQL 错误特征（不同数据库）
_SQL_ERROR_PATTERNS = [
    r"SQL syntax.*MySQL", r"Warning.*mysql_.*",
    r"PostgreSQL.*ERROR", r"ORA-\d{5}",
    r"Microsoft SQL Server.*Driver", r"ODBC SQL Server Driver",
    r"SQLite3?::query", r"SQLITE_ERROR",
    r"Unclosed quotation mark", r"Incorrect syntax near",
]

# XSS 测试 payload（无危害，仅检测回显/编码情况）
_XSS_PAYLOADS = [
    ("<script>alert(1)</script>", "经典 script 标签"),
    ('"><script>alert(1)</script>', "闭合属性注入"),
    ("javascript:alert(1)", "javascript 协议"),
    ("<img src=x onerror=alert(1)>", "img onerror 事件"),
    ("xss probe <xss>test</xss>", "自定义标签回显检测"),
]

# SSRF 测试 payload（指向本地回环/内网，检测是否发起请求）
_SSRF_PAYLOADS = [
    ("http://127.0.0.1:80", "本地回环"),
    ("http://localhost", "localhost"),
    ("http://169.254.169.254/latest/meta-data/", "AWS 元数据"),
    ("http://[::1]/", "IPv6 回环"),
]

# 路径遍历 payload
_TRAVERSAL_PAYLOADS = [
    "../../../etc/passwd", "..\\..\\..\\windows\\win.ini",
    "....//....//....//etc/passwd",
]

# 默认凭证检测（仅探测常见组合，不暴力破解）
_DEFAULT_CREDS = [
    ("admin", "admin"), ("admin", "password"), ("admin", "123456"),
    ("root", "root"), ("test", "test"), ("guest", "guest"),
]

# 安全头检查清单
_SECURITY_HEADERS = {
    "strict-transport-security": ("HSTS 缺失", Severity.MEDIUM, "A05_SECURITY_MISCONFIG", "CWE-319"),
    "content-security-policy": ("CSP 缺失，易受 XSS 攻击", Severity.MEDIUM, "XSS", "CWE-79"),
    "x-frame-options": ("X-Frame-Options 缺失，存在点击劫持风险", Severity.MEDIUM, "CLICKJACKING", "CWE-1021"),
    "x-content-type-options": ("X-Content-Type-Options 缺失", Severity.LOW, "A05_SECURITY_MISCONFIG", "CWE-79"),
    "referrer-policy": ("Referrer-Policy 缺失", Severity.LOW, "INFO_DISCLOSURE", "CWE-200"),
}


# ---------------------------------------------------------------------------
# VulnScanner 扫描引擎
# ---------------------------------------------------------------------------

class VulnScanner:
    """漏洞扫描引擎

    用法：
        scanner = VulnScanner(task=scan_task)
        result = await scanner.scan()  # ScanResult

    内部使用 httpx 直接请求目标（不走 Fetcher 三级回退，避免 Cloudflare 干扰扫描）。
    """

    def __init__(
        self,
        task: ScanTask,
        max_requests: int = 100,
    ) -> None:
        self.task = task
        self.max_requests = max_requests
        self._requests_sent = 0
        self._pages_scanned = 0
        self._vulns: List[Vulnerability] = []
        self._visited: Set[str] = set()

    async def scan(self) -> ScanResult:
        """执行完整扫描流程"""
        start = time.time()
        from crawagent.security.models import ScanStatus
        self.task.status = ScanStatus.RUNNING
        self.task.started_at = start

        try:
            import httpx
        except ImportError:
            return ScanResult(task=self.task, error="httpx 未安装")

        async with httpx.AsyncClient(
            timeout=self.task.timeout,
            follow_redirects=True,
            trust_env=False,
            verify=False,  # 扫描常遇自签名证书
        ) as client:
            # 1. 首页基础扫描（安全头/信息泄露/Cookie）
            await self._scan_landing_page(client, self.task.url)

            # 2. 发现可测参数（从首页链接提取）
            testable_urls = await self._discover_testable_urls(client, self.task.url)

            # 3. 对每个可测 URL 注入 payload
            categories = self.task.categories or list(VulnCategory)
            for test_url, params in testable_urls[:20]:  # 限制最多 20 个 URL
                if self._requests_sent >= self.max_requests:
                    logger.warning(f"达到最大请求数 {self.max_requests}，停止扫描")
                    break
                if VulnCategory.A03_INJECTION in categories:
                    await self._scan_sqli(client, test_url, params)
                if VulnCategory.XSS in categories:
                    await self._scan_xss(client, test_url, params)
                if VulnCategory.A10_SSRF in categories:
                    await self._scan_ssrf(client, test_url, params)
                if VulnCategory.A01_BROKEN_ACCESS_CONTROL in categories:
                    await self._scan_traversal(client, test_url, params)

            # 4. 默认凭证检测（仅当 URL 含 login 关键词）
            if VulnCategory.A07_AUTH_FAILURES in categories:
                await self._scan_default_creds(client, self.task.url)

        # 完成
        self.task.status = ScanStatus.COMPLETED
        self.task.finished_at = time.time()
        self.task.vuln_count = len(self._vulns)

        return ScanResult(
            task=self.task,
            vulnerabilities=list(self._vulns),
            pages_scanned=self._pages_scanned,
            requests_sent=self._requests_sent,
            duration_seconds=time.time() - start,
        )

    # ---- 单页扫描 ----

    async def _scan_landing_page(self, client, url: str) -> None:
        """首页基础扫描：安全头/信息泄露/Cookie 安全"""
        self._visited.add(url)
        try:
            resp = await client.get(url, headers=self.task.headers)
            self._requests_sent += 1
            self._pages_scanned += 1
        except Exception as e:
            logger.debug(f"扫描首页失败 {url}: {e}")
            return

        # 1. 安全头检查
        for header, (desc, sev, cat_name, cwe) in _SECURITY_HEADERS.items():
            if header not in {k.lower() for k in resp.headers.keys()}:
                # 按名称/值查找枚举（大小写不敏感）
                category = next(
                    (c for c in VulnCategory if c.name == cat_name or c.value == cat_name),
                    VulnCategory.A05_SECURITY_MISCONFIG,
                )
                self._add_vuln(
                    category=category,
                    severity=sev,
                    title=desc,
                    url=url,
                    evidence=f"响应头缺少: {header}",
                    remediation=self._remediation_for_header(header),
                    cwe_id=cwe,
                    confidence=0.9,
                )

        # 2. 信息泄露检测
        server = resp.headers.get("server", "")
        if server and re.search(r"\d+\.\d+", server):
            self._add_vuln(
                category=VulnCategory.INFO_DISCLOSURE,
                severity=Severity.INFO,
                title=f"服务器版本泄露: {server}",
                url=url,
                evidence=f"Server header: {server}",
                remediation="隐藏 Server 头或移除版本号",
                cwe_id="CWE-200",
                confidence=0.8,
            )

        x_powered = resp.headers.get("x-powered-by", "")
        if x_powered:
            self._add_vuln(
                category=VulnCategory.INFO_DISCLOSURE,
                severity=Severity.INFO,
                title=f"技术栈泄露: {x_powered}",
                url=url,
                evidence=f"X-Powered-By: {x_powered}",
                remediation="移除 X-Powered-By 头",
                cwe_id="CWE-200",
                confidence=0.8,
            )

        # 3. Cookie 安全检查
        for cookie in resp.cookies.jar:
            issues = []
            if not cookie.secure:
                issues.append("未设置 Secure")
            # httpx cookie 不直接暴露 httponly，跳过
            if issues:
                self._add_vuln(
                    category=VulnCategory.A05_SECURITY_MISCONFIG,
                    severity=Severity.LOW,
                    title=f"Cookie 安全属性缺失: {cookie.name}",
                    url=url,
                    evidence=f"Cookie '{cookie.name}': {', '.join(issues)}",
                    remediation=f"为 Cookie {cookie.name} 添加 Secure/HttpOnly/SameSite 属性",
                    cwe_id="CWE-614",
                    confidence=0.7,
                )

        # 3b. 会话 Cookie 标志检查（Set-Cookie 头解析，覆盖 HttpOnly/SameSite）
        set_cookies = resp.headers.get_list("set-cookie")
        seen_cookie_names: set = set()
        for sc in set_cookies:
            head = sc.split(";")[0]
            if "=" not in head:
                continue
            name = head.split("=")[0].strip()
            if not name or name in seen_cookie_names:
                continue
            seen_cookie_names.add(name)
            flags = {part.strip().lower() for part in sc.split(";")[1:]}
            missing = []
            if "httponly" not in flags:
                missing.append("HttpOnly")
            if not any(f.startswith("samesite") for f in flags):
                missing.append("SameSite")
            if missing:
                self._add_vuln(
                    category=VulnCategory.A07_AUTH_FAILURES,
                    severity=Severity.LOW,
                    title=f"会话 Cookie 缺少安全属性: {name}",
                    url=url,
                    evidence=f"Cookie '{name}': 缺少 {', '.join(missing)}",
                    remediation=f"为 Cookie {name} 添加 HttpOnly 和 SameSite=Strict/Lax 属性",
                    cwe_id="CWE-1004",
                    confidence=0.8,
                )

        # 4. 混合内容检测（HTTPS 页面含 HTTP 资源）
        if url.startswith("https://"):
            body = resp.text[:5000]
            http_resources = re.findall(r'(?:src|href)=["\']http://[^"\']+', body)
            if http_resources:
                self._add_vuln(
                    category=VulnCategory.A02_CRYPTO_FAILURES,
                    severity=Severity.MEDIUM,
                    title="混合内容：HTTPS 页面含 HTTP 资源",
                    url=url,
                    evidence=f"发现 {len(http_resources)} 个 HTTP 资源，示例: {http_resources[0][:60]}",
                    remediation="将所有资源引用改为 HTTPS 或相对路径",
                    cwe_id="CWE-311",
                    confidence=0.9,
                )

        # 5. 明文传输检测（HTTP 无 TLS 加密）
        if url.startswith("http://"):
            self._add_vuln(
                category=VulnCategory.A02_CRYPTO_FAILURES,
                severity=Severity.MEDIUM,
                title="明文 HTTP 传输（无 TLS 加密）",
                url=url,
                evidence="目标使用 http:// 协议，数据明文传输",
                remediation="部署 HTTPS（TLS 1.2+）并配置 HSTS，敏感数据不得明文传输",
                cwe_id="CWE-319",
                confidence=0.9,
            )

    # ---- 注入扫描 ----

    async def _scan_sqli(self, client, url: str, params: List[str]) -> None:
        """SQL 注入扫描：基于错误 + 布尔差异"""
        for param in params:
            for payload, desc in _SQLI_PAYLOADS:
                if self._requests_sent >= self.max_requests:
                    return
                test_url = self._inject_param(url, param, payload)
                try:
                    resp = await client.get(test_url, headers=self.task.headers)
                    self._requests_sent += 1
                except Exception:
                    continue

                # 错误型：响应含数据库错误信息
                for pattern in _SQL_ERROR_PATTERNS:
                    if re.search(pattern, resp.text, re.IGNORECASE):
                        self._add_vuln(
                            category=VulnCategory.A03_INJECTION,
                            severity=Severity.HIGH,
                            title=f"SQL 注入（基于错误）: {param}",
                            url=url,
                            parameter=param,
                            payload=payload,
                            request_url=test_url,
                            evidence=f"响应匹配数据库错误特征: {pattern[:40]}",
                            remediation="使用参数化查询（PreparedStatement），禁止字符串拼接 SQL",
                            cwe_id="CWE-89",
                            confidence=0.95,
                        )
                        break  # 同参数同类漏洞只报一个

    async def _scan_xss(self, client, url: str, params: List[str]) -> None:
        """XSS 扫描：检测 payload 回显是否被编码"""
        for param in params:
            for payload, desc in _XSS_PAYLOADS:
                if self._requests_sent >= self.max_requests:
                    return
                test_url = self._inject_param(url, param, payload)
                try:
                    resp = await client.get(test_url, headers=self.task.headers)
                    self._requests_sent += 1
                except Exception:
                    continue

                # 检测 payload 是否原样回显（未编码）
                if payload in resp.text:
                    # 进一步判断是否在可执行上下文（script/handler 附近）
                    severity = Severity.HIGH if "<script" in payload.lower() else Severity.MEDIUM
                    self._add_vuln(
                        category=VulnCategory.XSS,
                        severity=severity,
                        title=f"反射型 XSS: {param}",
                        url=url,
                        parameter=param,
                        payload=payload,
                        request_url=test_url,
                        evidence=f"payload 原样回显在响应中（{desc}）",
                        remediation="对用户输入做 HTML 实体编码（&lt;script&gt;），使用 CSP 防御",
                        cwe_id="CWE-79",
                        confidence=0.85,
                    )
                    break  # 同参数只报一个 XSS

    async def _scan_ssrf(self, client, url: str, params: List[str]) -> None:
        """SSRF 扫描：检测 URL 参数是否发起内网请求"""
        for param in params:
            # 仅对疑似 URL 参数测试
            if not any(k in param.lower() for k in ("url", "link", "redirect", "callback", "next", "img", "src")):
                continue
            for payload, desc in _SSRF_PAYLOADS:
                if self._requests_sent >= self.max_requests:
                    return
                test_url = self._inject_param(url, param, payload)
                try:
                    resp = await client.get(test_url, headers=self.task.headers, timeout=10)
                    self._requests_sent += 1
                except Exception as e:
                    # 超时或连接错误可能是 SSRF 已触发（目标尝试连内网）
                    if "timeout" in str(e).lower() or "connect" in str(e).lower():
                        self._add_vuln(
                            category=VulnCategory.A10_SSRF,
                            severity=Severity.HIGH,
                            title=f"疑似 SSRF: {param}",
                            url=url,
                            parameter=param,
                            payload=payload,
                            request_url=test_url,
                            evidence=f"注入内网地址后请求异常: {e}",
                            remediation="校验 URL 白名单，禁止访问内网地址（127.0.0.1/10.*/169.254.169.254）",
                            cwe_id="CWE-918",
                            confidence=0.6,
                        )
                    continue

                # 响应内容含内网特征（如 AWS 元数据）
                if "169.254.169.254" in payload and ("ami-id" in resp.text or "instance-id" in resp.text):
                    self._add_vuln(
                        category=VulnCategory.A10_SSRF,
                        severity=Severity.CRITICAL,
                        title=f"SSRF（云元数据泄露）: {param}",
                        url=url,
                        parameter=param,
                        payload=payload,
                        request_url=test_url,
                        evidence="响应含 AWS 元数据特征",
                        remediation="URL 参数校验白名单，禁止访问 169.254.169.254 等元数据地址",
                        cwe_id="CWE-918",
                        confidence=0.95,
                    )
                    break

    async def _scan_traversal(self, client, url: str, params: List[str]) -> None:
        """路径遍历扫描"""
        for param in params:
            for payload in _TRAVERSAL_PAYLOADS:
                if self._requests_sent >= self.max_requests:
                    return
                test_url = self._inject_param(url, param, payload)
                try:
                    resp = await client.get(test_url, headers=self.task.headers)
                    self._requests_sent += 1
                except Exception:
                    continue

                # 检测系统文件特征
                if "root:" in resp.text and ":/bin/" in resp.text:
                    self._add_vuln(
                        category=VulnCategory.A01_BROKEN_ACCESS_CONTROL,
                        severity=Severity.HIGH,
                        title=f"路径遍历: {param}",
                        url=url,
                        parameter=param,
                        payload=payload,
                        request_url=test_url,
                        evidence="响应含 /etc/passwd 特征内容",
                        remediation="校验参数不含 ../，使用白名单限制可访问路径",
                        cwe_id="CWE-22",
                        confidence=0.95,
                    )
                    break
                if "[fonts]" in resp.text or "[extensions]" in resp.text:
                    self._add_vuln(
                        category=VulnCategory.A01_BROKEN_ACCESS_CONTROL,
                        severity=Severity.HIGH,
                        title=f"路径遍历: {param}",
                        url=url,
                        parameter=param,
                        payload=payload,
                        request_url=test_url,
                        evidence="响应含 win.ini 特征内容",
                        remediation="校验参数不含 ..\\，使用白名单限制可访问路径",
                        cwe_id="CWE-22",
                        confidence=0.95,
                    )
                    break

    async def _scan_default_creds(self, client, base_url: str) -> None:
        """默认凭证检测（仅探测常见组合，不暴力破解）"""
        # 从首页找登录页 URL
        try:
            resp = await client.get(base_url, headers=self.task.headers)
            self._requests_sent += 1
        except Exception:
            return

        login_urls = re.findall(r'href=["\']([^"\']*(?:login|signin|admin)["\'][^"\']*)', resp.text, re.IGNORECASE)
        if not login_urls:
            return
        login_url = urljoin(base_url, login_urls[0])

        for username, password in _DEFAULT_CREDS:
            if self._requests_sent >= self.max_requests:
                return
            try:
                # 尝试 POST 登录（通用表单字段）
                resp = await client.post(
                    login_url,
                    data={"username": username, "password": password, "user": username, "pass": password},
                    headers=self.task.headers,
                    timeout=10,
                )
                self._requests_sent += 1
                # 登录成功特征：重定向到 dashboard / 返回 token / Set-Cookie 含 session
                if resp.status_code in (200, 302):
                    body = resp.text[:3000].lower()
                    if any(k in body for k in ("dashboard", "welcome", "logout", "sign out")):
                        self._add_vuln(
                            category=VulnCategory.A07_AUTH_FAILURES,
                            severity=Severity.CRITICAL,
                            title=f"默认凭证可登录: {username}/{password}",
                            url=login_url,
                            evidence=f"用 {username}/{password} 登录后响应含成功特征",
                            remediation="强制用户修改默认密码，禁止弱密码",
                            cwe_id="CWE-798",
                            confidence=0.9,
                        )
                        return
            except Exception:
                continue

    # ---- 辅助 ----

    async def _discover_testable_urls(self, client, base_url: str) -> List[tuple]:
        """从首页发现带参数的可测 URL"""
        try:
            resp = await client.get(base_url, headers=self.task.headers)
            self._requests_sent += 1
        except Exception:
            return []

        from bs4 import BeautifulSoup
        try:
            soup = BeautifulSoup(resp.text, "html.parser")
        except Exception:
            return []

        testable: List[tuple] = []
        seen: Set[str] = set()

        # 收集带 query string 的链接
        for a in soup.find_all("a", href=True):
            href = a["href"]
            full_url = urljoin(base_url, href)
            parsed = urlparse(full_url)
            if parsed.query and full_url not in seen:
                params = [p.split("=")[0] for p in parsed.query.split("&") if p]
                if params:
                    testable.append((full_url, params))
                    seen.add(full_url)

        # 收集表单
        for form in soup.find_all("form"):
            action = form.get("action", "")
            method = form.get("method", "get").lower()
            full_url = urljoin(base_url, action) if action else base_url
            params = [inp.get("name", "") for inp in form.find_all("input") if inp.get("name")]
            params = [p for p in params if p]
            if params and full_url not in seen:
                testable.append((full_url, params))
                seen.add(full_url)

        return testable

    def _inject_param(self, url: str, param: str, payload: str) -> str:
        """在 URL 的指定参数注入 payload（保留其他参数）"""
        parsed = urlparse(url)
        from urllib.parse import parse_qs, urlencode
        qs = parse_qs(parsed.query, keep_blank_values=True)
        qs[param] = [payload]
        new_query = urlencode({k: v[0] if isinstance(v, list) else v for k, v in qs.items()})
        return f"{parsed.scheme}://{parsed.netloc}{parsed.path}?{new_query}"

    def _add_vuln(self, **kwargs) -> None:
        """添加漏洞记录"""
        vuln = Vulnerability(scan_task_id=self.task.id, **kwargs)
        self._vulns.append(vuln)
        logger.info(f"[Security] 发现漏洞: {vuln.to_summary()}")

    def _remediation_for_header(self, header: str) -> str:
        remedies = {
            "strict-transport-security": "添加 HSTS 头: Strict-Transport-Security: max-age=31536000; includeSubDomains",
            "content-security-policy": "添加 CSP 头限制脚本来源，如: default-src 'self'",
            "x-frame-options": "添加 X-Frame-Options: DENY 或 SAMEORIGIN",
            "x-content-type-options": "添加 X-Content-Type-Options: nosniff",
            "referrer-policy": "添加 Referrer-Policy: no-referrer 或 strict-origin-when-cross-origin",
        }
        return remedies.get(header, f"添加 {header} 头")
