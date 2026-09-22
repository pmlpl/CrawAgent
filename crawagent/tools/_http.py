"""HTTP 共享 helper — 节流 / 默认 UA / encoding 修正 / 错误转发一处生效。

设计（变更 020）：所有"返回 text 的标准 GET"模式都走 ``http_get()``。
特殊路径（stream=True / 需要 Response 对象 / 特殊 proxies）保留内联 ``requests.get``。

注：
- ``DEFAULT_UA`` 和 ``_enforce_delay`` 来自 ``crawl_tool``（避免重新定义全局节流状态）。
- 模块名 ``_http`` 是有意下划线前缀——helper 模块，非工具，``discover_tools()`` 自然不收。
"""
from __future__ import annotations

import requests

from crawagent.tools.crawl_tool import DEFAULT_UA, _enforce_delay

DEFAULT_TIMEOUT = 20


def http_get(
    url: str,
    *,
    headers: dict | None = None,
    params: dict | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> str:
    """统一 GET：节流 + 默认 UA + 显式 timeout + 自动 encoding 修正 + 抛 HTTPError。

    行为：
    1. 调用 ``_enforce_delay()`` 强制间隔（来自 ``crawl_tool`` 的全局节流器）。
    2. 合并 headers：``User-Agent`` 默认 ``DEFAULT_UA``，caller headers 在后覆盖。
    3. ``params`` 直接透传给 ``requests.get``（拼到 URL query string）。
    4. ``raise_for_status()`` → HTTPError 上抛。
    5. 用 ``apparent_encoding`` 修正 encoding（fallback utf-8），与原 ``crawl_webpage`` 行为一致。
    6. 返回 ``resp.text``。

    Args:
        url: 目标 URL。
        headers: 可选 caller 自定义 headers；``User-Agent`` 缺省时用 ``DEFAULT_UA``。
        params: URL query 参数（透传给 ``requests.get``）。
        timeout: 单次请求超时秒数，默认 20。

    Returns:
        解码后的响应文本。

    Raises:
        requests.HTTPError: HTTP 4xx/5xx 时 ``raise_for_status`` 抛出。
        requests.Timeout / ConnectionError: 网络异常透传。

    范围外（不替换）：
    - 需要 ``Response`` 对象的调用点（``.json()`` / ``.url`` / ``.status_code``）
    - 需要 ``resp.content``（bytes）的下载（如 font 字体二进制）
    - ``stream=True`` 的大文件下载
    - 特殊 ``proxies=...`` 的代理探测
    """
    _enforce_delay()
    final_headers = {"User-Agent": DEFAULT_UA, **(headers or {})}
    resp = requests.get(url, params=params, headers=final_headers, timeout=timeout)
    resp.raise_for_status()
    # encoding 修正：与 crawl_webpage 原实现一致（避免 latin-1 误判）
    resp.encoding = resp.apparent_encoding or "utf-8"
    return resp.text