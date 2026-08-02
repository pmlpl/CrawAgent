"""P5 安全扫描验收测试：本地站点扫描 / Hook 拦截 / 补丁生成。"""

import asyncio

from crawagent.security.auto_fixer import AutoFixer
from crawagent.security.models import (
    ScanResult,
    ScanTask,
    Severity,
    VulnCategory,
    Vulnerability,
    SecurityStore,
)
from crawagent.security.security_hook import SecurityHookHandler
from crawagent.security.vuln_scanner import VulnScanner


def _await(coro):
    return asyncio.run(coro)


def _build_unsafe_site(tmp_path):
    """构造一个缺少安全头、含敏感信息的测试站点。"""
    (tmp_path / "index.html").write_text(
        """<html><head><title>测试站点</title>
        <meta name="generator" content="Django 1.8">
        </head><body>
        <!-- 管理员密码: admin123（开发环境遗留） -->
        <form action="/login" method="post">
          <input name="username"><input name="password">
        </form>
        <a href="/admin">后台</a>
        <a href="/search?id=1">搜索</a>
        </body></html>""",
        encoding="utf-8",
    )
    (tmp_path / "admin").write_text("<html><body>管理后台（未配置认证）</body></html>", encoding="utf-8")
    (tmp_path / "robots.txt").write_text("User-agent: *\nDisallow: /secret\n", encoding="utf-8")
    (tmp_path / "secret").write_text("secret_key = 'sk-1234567890abcdef'", encoding="utf-8")


def test_vuln_scanner_finds_issues(local_server):
    base_url, tmp_path = local_server
    _build_unsafe_site(tmp_path)

    task = ScanTask(
        name="本地扫描",
        url=f"{base_url}/index.html",
        categories=[
            VulnCategory.A05_SECURITY_MISCONFIG,
            VulnCategory.INFO_DISCLOSURE,
            VulnCategory.XSS,
        ],
        timeout=10,
    )
    scanner = VulnScanner(task=task, max_requests=60)
    result: ScanResult = _await(scanner.scan())
    assert result.task.status.value == "completed"
    assert result.pages_scanned >= 1
    assert len(result.vulnerabilities) >= 3, [
        v.to_summary() for v in result.vulnerabilities
    ]
    # 至少覆盖 2 个类别（验收：报告至少 5 类风险需要更多目标；本地站验证引擎可用）
    categories = {v.category for v in result.vulnerabilities}
    assert VulnCategory.INFO_DISCLOSURE in categories
    assert VulnCategory.A05_SECURITY_MISCONFIG in categories


def test_vuln_scanner_graceful_on_dead_url(local_server):
    base_url, _tmp = local_server
    task = ScanTask(url=f"{base_url}/not-exist.html", categories=[VulnCategory.INFO_DISCLOSURE], timeout=10)
    result = _await(VulnScanner(task=task, max_requests=20).scan())
    assert result is not None
    assert result.task.status.value == "completed"


def test_vuln_scanner_cookie_flags_a07(local_server):
    """回归：Set-Cookie 缺 HttpOnly/SameSite 应归为 A07 认证失败。"""
    import http.server
    import threading

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body = b"<html><body>ok</body></html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Set-Cookie", "session=abc123; Path=/")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}/"
        task = ScanTask(url=url, categories=[VulnCategory.A07_AUTH_FAILURES], timeout=10)
        result = _await(VulnScanner(task=task, max_requests=20).scan())
        a07 = [v for v in result.vulnerabilities if v.category == VulnCategory.A07_AUTH_FAILURES]
        assert len(a07) >= 1, [v.to_summary() for v in result.vulnerabilities]
        assert "session" in a07[0].title
    finally:
        server.shutdown()


def test_security_hook_blocks_dangerous():
    hook = SecurityHookHandler()

    async def run(name, args):
        ctx = {"tool_calls": [{"function": {"name": name, "arguments": dict(args)}}]}
        return await hook.before_tool(ctx)

    # 生产环境 scan_vuln → 标记需确认（不硬拦截）
    ctx = _await(run("scan_vuln", {"url": "https://www.apple.com/"}))
    args = ctx["tool_calls"][0]["function"]["arguments"]
    assert args.get("_security_needs_confirmation") is True
    assert args.get("_security_blocked") is not True

    # 敏感路径保存 → 硬拦截
    ctx3 = _await(run("save", {"path": "/etc/passwd"}))
    args3 = ctx3["tool_calls"][0]["function"]["arguments"]
    assert args3.get("_security_blocked") is True

    # 普通调用 → 不拦截
    assert _await(run("save", {"path": "/tmp/ok.md"})) is None


def test_security_store_crud(tmp_path):
    store = SecurityStore(db_path=str(tmp_path / "security.db"))
    task = ScanTask(url="https://example.com")
    store.create_task(task)
    assert store.get_task(task.id) is not None
    assert len(store.list_tasks()) == 1
    vuln = Vulnerability(scan_task_id=task.id, category=VulnCategory.XSS, severity=Severity.HIGH,
                         title="反射型 XSS", url="https://example.com/search")
    store.add_vulnerability(vuln)
    vulns = store.list_vulnerabilities(task_id=task.id)
    assert len(vulns) == 1 and vulns[0].title == "反射型 XSS"


def test_auto_fixer_generates_patch(tmp_path):
    fixer = AutoFixer()
    vuln = Vulnerability(
        category=VulnCategory.A05_SECURITY_MISCONFIG,
        severity=Severity.MEDIUM,
        title="缺少 X-Frame-Options 响应头",
        url="https://example.com",
        remediation="在响应头中添加 X-Frame-Options: DENY",
    )
    patches, reported_only = _await(fixer.generate_patches([vuln]))
    assert len(patches) >= 1
    assert len(reported_only) == 0  # MEDIUM 在白名单内，自动生成补丁
    patch = patches[0]
    assert patch.target_file
    assert "diff" in patch.diff or patch.diff

    files = _await(fixer.save_patches(patches, str(tmp_path)))
    assert files and __import__("os").path.isfile(files[0])


def test_auto_fixer_high_severity_reports_only(tmp_path):
    """高危漏洞默认只报告，不自动生成补丁（等级白名单）。"""
    fixer = AutoFixer()
    vuln = Vulnerability(
        category=VulnCategory.A03_INJECTION,
        severity=Severity.HIGH,
        title="SQL 注入",
        url="https://example.com/item?id=1",
    )
    patches, reported_only = _await(fixer.generate_patches([vuln]))
    assert len(patches) == 0          # 高危不自动 patch
    assert len(reported_only) == 1    # 仅报告

    # 显式允许高危 → 生成补丁
    from crawagent.security.models import Severity as Sev
    patches2, reported2 = _await(fixer.generate_patches(
        [vuln], allowed_severities=[Sev.LOW, Sev.MEDIUM, Sev.HIGH]
    ))
    assert len(patches2) >= 1
    assert len(reported2) == 0
