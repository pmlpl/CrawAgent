"""爬取工具 — requests + fake-useragent 抓取网页 HTML"""
import re
import time
from langchain_core.tools import tool
from fake_useragent import UserAgent
import requests
from crawagent.config.settings import get_settings

# 全局 UserAgent 生成器
_ua = UserAgent()

# 共享默认 UA（供 weread_tool 等复用，避免每个工具各自硬编码）
DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

# 上次请求时间，用于强制间隔
_last_request_time: float = 0


def _enforce_delay() -> None:
    """强制请求间隔，避免高频请求被封"""
    settings = get_settings()
    global _last_request_time
    elapsed = time.time() - _last_request_time
    if elapsed < settings.request_delay:
        time.sleep(settings.request_delay - elapsed)
    _last_request_time = time.time()


@tool
def crawl_webpage(url: str) -> str:
    """请求目标 URL 的 HTML 原始内容（requests 直连，不渲染 JS）。

    参数：
        url: 目标 URL，必须带 http:// 或 https://

    返回：
        成功：HTML 源码字符串（超长时会被截断，末尾附 "... [已截断]"）。
        失败："抓取失败：<错误详情>"。
        若命中 SPA 空壳返回："[SPA_SHELL_DETECTED] ..."，提示用 browse_and_crawl 渲染。
    """
    settings = get_settings()
    _enforce_delay()

    headers = {
        "User-Agent": _ua.random,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }

    try:
        resp = requests.get(
            url,
            headers=headers,
            timeout=settings.request_timeout,
        )
        resp.raise_for_status()
        resp.encoding = resp.apparent_encoding or "utf-8"
        html = resp.text

        # 检测 SPA shell：HTML 很短、主要是 <script> 标签、没有实际内容
        body_text = re.sub(r"<[^>]+>", "", html).strip()
        is_spa_shell = (
            len(html) < 5000
            and len(body_text) < 200
            and html.count("<script") >= 1
        )
        if is_spa_shell:
            return (
                f"[SPA_SHELL_DETECTED] The page at {url} appears to be a JavaScript SPA. "
                f"Static crawling returned only {len(html)} bytes with {len(body_text)} chars of visible text. "
                f"SPA shell content follows:\n\n{html[:2000]}"
            )

        # Truncate overly long HTML to avoid token bloat
        if len(html) > settings.max_content_length:
            html = html[: settings.max_content_length] + "\n... [content truncated]"
        return html
    except requests.RequestException as e:
        return f"ERROR: Crawl failed for {url} — {e}"
