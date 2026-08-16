"""爬取工具 — requests + fake-useragent 抓取网页 HTML"""
import time
from langchain_core.tools import tool
from fake_useragent import UserAgent
import requests
from crawagent.config.settings import get_settings

# 全局 UserAgent 生成器
_ua = UserAgent()

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
    """Fetch the HTML content of a webpage at the given URL.

    Args:
        url: The target URL, must include http:// or https://

    Returns:
        On success: the raw HTML source string (may be truncated if exceeding max_content_length, appended with "... [content truncated]").
        On failure: "Crawl failed: <error details>".
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

        # Truncate overly long HTML to avoid token bloat
        if len(html) > settings.max_content_length:
            html = html[: settings.max_content_length] + "\n... [content truncated]"
        return html
    except requests.RequestException as e:
        return f"Crawl failed: {e}"
