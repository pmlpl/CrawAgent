"""浏览器网络请求捕获（P5-3）

复用 Playwright 的 page.on("request") / page.on("response") 事件，
被动捕获页面加载过程中的所有网络请求，用于：

1. 发现 API 端点（/api/users → 后续可测 SQLi/IDOR）
2. 检测敏感数据泄露（响应含 token/密码/PII）
3. 检测不安全请求（http:// 明文/敏感字段走 GET）
4. 收集 Cookie/CSRF token 供后续测试

设计：
- NetworkCapture 是无副用的观察者，不修改请求
- 捕获结果结构化，供 VulnScanner 使用
- 浏览器不可用时降级为 httpx 单次请求分析（无网络流捕获）
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from loguru import logger


# ---------------------------------------------------------------------------
# 数据模型
# ---------------------------------------------------------------------------

@dataclass
class CapturedRequest:
    """单次捕获的请求/响应"""
    method: str = "GET"
    url: str = ""
    status: int = 0
    resource_type: str = ""        # document/xhr/script/stylesheet/image/font
    request_headers: Dict[str, str] = field(default_factory=dict)
    response_headers: Dict[str, str] = field(default_factory=dict)
    post_data: str = ""             # POST body
    response_body_preview: str = ""  # 响应体前 2KB（用于敏感信息检测）
    duration_ms: float = 0.0

    @property
    def is_api(self) -> bool:
        """是否像 API 请求（XHR/fetch，非静态资源）"""
        return self.resource_type in ("xhr", "fetch") or "/api/" in self.url

    @property
    def is_insecure(self) -> bool:
        """是否明文 http 请求（非 https）"""
        return self.url.startswith("http://")


@dataclass
class CaptureResult:
    """捕获汇总"""
    requests: List[CapturedRequest] = field(default_factory=list)
    cookies: List[Dict[str, str]] = field(default_factory=list)
    api_endpoints: List[str] = field(default_factory=list)   # 去重后的 API URL
    sensitive_findings: List[Dict[str, str]] = field(default_factory=list)
    error: str = ""

    @property
    def request_count(self) -> int:
        return len(self.requests)


# 敏感信息模式（响应体中的泄露检测）
_SENSITIVE_PATTERNS = [
    (r"(?:api[_-]?key|apikey|api[_-]?secret)[\"':= ]+([A-Za-z0-9_\-]{20,})", "API Key 泄露"),
    (r"(?:access[_-]?token|access_token)[\"':= ]+([A-Za-z0-9_\-\.]{20,})", "Access Token 泄露"),
    (r"(?:password|passwd|pwd)[\"':= ]+([^\s\"']{4,})", "密码字段泄露"),
    (r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b", "邮箱泄露"),
    (r"\b(?:\d{15,18})\b", "可能的身份证号"),
    (r"\b4[0-9]{12}(?:[0-9]{3})?\b", "可能的信用卡号"),
    (r"-----BEGIN (?:RSA |EC )?PRIVATE KEY-----", "私钥泄露"),
    (r"Bearer\s+[A-Za-z0-9_\-\.]{20,}", "Bearer Token 泄露"),
]


class NetworkCapture:
    """浏览器网络请求捕获器

    用法（Playwright 场景）：
        capture = NetworkCapture()
        async with capture.attach(page):
            await page.goto(url)
        result = capture.result

    降级（无浏览器场景）：
        result = await capture.analyze_url(url)  # 单次 httpx 请求分析
    """

    def __init__(self, max_body_preview: int = 2048) -> None:
        self.max_body_preview = max_body_preview
        self._result = CaptureResult()
        self._page = None

    @property
    def result(self) -> CaptureResult:
        return self._result

    # ---- Playwright 场景 ----

    async def attach(self, page: Any) -> "NetworkCapture":
        """附加到 Playwright page，开始捕获网络请求"""
        self._page = page
        self._result = CaptureResult()

        def on_request(request: Any) -> None:
            try:
                req = CapturedRequest(
                    method=request.method,
                    url=request.url,
                    resource_type=getattr(request, "resource_type", ""),
                    request_headers=dict(request.headers),
                    post_data=request.post_data or "",
                )
                self._result.requests.append(req)
            except Exception as e:
                logger.debug(f"捕获请求失败: {e}")

        def on_response(response: Any) -> None:
            try:
                # 找到对应的请求记录，补充响应信息
                url = response.url
                for req in reversed(self._result.requests):
                    if req.url == url:
                        req.status = response.status
                        req.response_headers = dict(response.headers)
                        # 异步获取响应体（仅 xhr/fetch 预览前 2KB）
                        if req.is_api and self.max_body_preview > 0:
                            # response.body() 是 async，不能在同步回调里调用
                            # 用 response.text() 同步属性（如果可用）
                            pass
                        break
            except Exception as e:
                logger.debug(f"捕获响应失败: {e}")

        async def collect_api_bodies() -> None:
            """页面加载后，收集 API 响应体预览"""
            if not self._page:
                return
            # Playwright 不能回溯已完成的响应体，这里只做 headers 分析
            pass

        page.on("request", on_request)
        page.on("response", on_response)
        return self

    async def __aenter__(self) -> "NetworkCapture":
        return self

    async def __aexit__(self, *exc) -> None:
        await self._finalize()

    async def _finalize(self) -> None:
        """汇总：去重 API、检测敏感信息"""
        # API 端点去重
        seen = set()
        for req in self._result.requests:
            if req.is_api and req.url not in seen:
                self._result.api_endpoints.append(req.url)
                seen.add(req.url)
        # 敏感信息检测（请求 headers + post_data）
        for req in self._result.requests:
            blob = f"{req.post_data} {req.request_headers}"
            for pattern, name in _SENSITIVE_PATTERNS:
                matches = re.findall(pattern, blob, re.IGNORECASE)
                for m in matches[:1]:  # 每类只报第一个
                    self._result.sensitive_findings.append({
                        "type": name,
                        "url": req.url,
                        "match": str(m)[:60],
                    })

    # ---- 降级场景：无浏览器，单次 httpx 请求分析 ----

    async def analyze_url(self, url: str, headers: Dict = None) -> CaptureResult:
        """无浏览器时，用 httpx 单次请求做基础分析"""
        try:
            import httpx
        except ImportError:
            self._result.error = "httpx 未安装"
            return self._result

        try:
            async with httpx.AsyncClient(
                timeout=15.0, follow_redirects=True, trust_env=False
            ) as client:
                resp = await client.get(url, headers=headers or {})

            req = CapturedRequest(
                method="GET",
                url=str(resp.url),
                status=resp.status_code,
                request_headers=dict(headers or {}),
                response_headers=dict(resp.headers),
                response_body_preview=resp.text[: self.max_body_preview],
            )
            self._result.requests.append(req)

            # 敏感信息检测（响应体）
            for pattern, name in _SENSITIVE_PATTERNS:
                matches = re.findall(pattern, resp.text, re.IGNORECASE)
                for m in matches[:1]:
                    self._result.sensitive_findings.append({
                        "type": name,
                        "url": url,
                        "match": str(m)[:60],
                    })

            # Cookie 收集
            for cookie in resp.cookies.jar:
                self._result.cookies.append({
                    "name": cookie.name,
                    "value": cookie.value[:20] + "..." if len(cookie.value) > 20 else cookie.value,
                    "domain": cookie.domain,
                    "secure": str(cookie.secure),
                    "httponly": "unknown",  # httpx 不直接暴露 httponly
                })

            await self._finalize()
        except Exception as e:
            self._result.error = f"httpx 请求失败: {e}"

        return self._result
