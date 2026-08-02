"""浏览器 API 捕获器（P9 强风控 / JS 逆向训练核心）

原理：
    强风控站点（抖音 / 小红书 / 大众点评等）的页面数据由前端 JS 调用签名 API
    获取（a_bogus / x-s 等签名参数由站点 JS 在运行时计算）。
    与其人工逆向签名算法，不如**让浏览器自己当签名机**：
    - Playwright 打开页面并执行站点 JS → 站点自动计算签名并发出 API 请求
    - 拦截 page "response" 事件 → 拿到带签名的真实 API 响应（含数据直链）
    - 滚动页面触发分页 → 收集更多 API 响应
    - 从 JSON 中启发式提取列表数据与媒体直链

优势：
    - 无需逆向任何签名算法（签名由浏览器运行时计算）
    - 对 API 型风控站点通用，不绑定特定网站
    - 自动携带匿名 cookie（访问时 JS 自动种下），进一步降低风控触发

注意：
    - 本机代理必须禁用（--no-proxy-server），避免被代理层拦截识别
    - 仅拦截响应体为 JSON 的 API 调用，页面本身不保存
"""
from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from loguru import logger

try:
    from playwright.async_api import async_playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False

# 默认 API 路径特征（URL 含这些片段视为数据接口）
_DEFAULT_API_HINTS = (
    "/api/", "/v1/", "/v2/", "/v3/", "/aweme/", "/web/", "/ajax/",
    "/search", "/feed", "/list", "/detail", "/recommend", "/video",
    ".json", "/graphql", "/gateway", "/query", "/discover",
)

# 常见数据容器键（value 是 list[dict] 时优先选用）
_DATA_LIST_KEYS = (
    "items", "records", "list", "data", "video_list", "aweme_list",
    "cardlist", "notes", "feeds", "results", "data_list", "contents",
)

# 媒体直链特征（key 或 URL 命中视为媒体地址）
_MEDIA_KEY_HINTS = ("play_addr", "url_list", "download_addr", "video", "mp4",
                    "m3u8", "audio", "playurl", "stream_url", "hls", "uri")
_MEDIA_EXT_RE = re.compile(r"\.(mp4|m3u8|flv|webm|mkv|mp3|m4a)([?&#]|$)", re.I)
# 媒体 CDN 域名特征（签名直链常无扩展名，靠域名识别）
_MEDIA_DOMAIN_RE = re.compile(
    r"(douyinvod|douyinstatic|byteimg|kwaicdn|kwai|hdslb|bilivideo|"
    r"aliyuncs|amazonaws|cloudfront|akamaized|cdn[a-z]*\.|"
    r"vod|playurl|media\.|upic|img[0-9a-z-]*\.|music)",
    re.I,
)

# 风控/验证码特征（标题或 body 命中则触发等待/重试）
_RISK_HINTS = ("验证", "captcha", "risk", "滑动", "安全验证", "访问异常",
               "human", "verify", "风控")


@dataclass
class ApiCapture:
    """单条捕获的 API 响应"""
    url: str
    status: int
    json: Optional[Dict[str, Any]] = None
    text_preview: str = ""


@dataclass
class HarvestResult:
    """API 捕获结果"""
    success: bool = False
    page_url: str = ""
    api_calls: List[ApiCapture] = field(default_factory=list)
    items: List[Dict[str, Any]] = field(default_factory=list)
    media_urls: List[Dict[str, str]] = field(default_factory=list)
    risk_detected: bool = False
    error: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "page_url": self.page_url,
            "api_calls": [
                {"url": c.url, "status": c.status, "text_preview": c.text_preview[:200]}
                for c in self.api_calls
            ],
            "items_count": len(self.items),
            "items": self.items[:200],
            "media_urls": self.media_urls,
            "risk_detected": self.risk_detected,
            "error": self.error,
        }


class BrowserAPIHarvester:
    """浏览器 API 捕获器

    用法:
        harvester = BrowserAPIHarvester()
        result = await harvester.harvest("https://www.douyin.com/", max_scrolls=5)
    """

    def __init__(self) -> None:
        self._api_hints = _DEFAULT_API_HINTS

    # ==================== 主入口 ====================

    async def harvest(
        self,
        url: str,
        *,
        api_patterns: Optional[List[str]] = None,
        max_scrolls: int = 5,
        scroll_wait_ms: int = 1500,
        max_responses: int = 100,
        max_body_bytes: int = 2_000_000,
        headless: bool = True,
        wait_ms: int = 6000,
        cookies: Optional[List[Dict[str, Any]]] = None,
        cookies_file: str = "",
    ) -> HarvestResult:
        """打开页面并拦截 API 响应

        Args:
            url: 目标页面 URL
            api_patterns: 额外的 API 路径特征（与默认特征取并集）
            max_scrolls: 滚动轮数（触发分页/懒加载 API）
            scroll_wait_ms: 每轮滚动后等待毫秒
            max_responses: 最多收集多少条 API 响应
            max_body_bytes: 单条响应体最大字节数（超限截断）
            headless: 是否无头模式
            wait_ms: 初次加载后的等待毫秒
            cookies: 注入的 cookie 列表（Playwright 格式，登录态/匿名 cookie 降低风控）
            cookies_file: Netscape cookies.txt 路径（与 cookies 二选一，自动转换注入）

        Returns:
            HarvestResult
        """
        if not PLAYWRIGHT_AVAILABLE:
            return HarvestResult(success=False, error="Playwright not installed")

        result = HarvestResult(page_url=url)
        captured: List[ApiCapture] = []
        patterns = list(api_patterns or []) + list(self._api_hints)

        # 从 cookies.txt 解析 Netscape 格式（供 add_cookies 注入）
        if cookies_file and not cookies:
            cookies = _parse_netscape_cookies(cookies_file)

        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=headless,
                args=["--disable-blink-features=AutomationControlled", "--no-proxy-server"],
            )
            try:
                context = await browser.new_context(
                    user_agent=(
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
                    ),
                    viewport={"width": 1280, "height": 900},
                    locale="zh-CN",
                )
                if cookies:
                    try:
                        await context.add_cookies(cookies)
                    except Exception as e:
                        logger.debug(f"[APIHarvester] cookie 注入失败: {e}")
                page = await context.new_page()

                # 拦截 XHR / fetch 响应
                async def _on_response(response) -> None:
                    if len(captured) >= max_responses:
                        return
                    resp_url = response.url
                    ctype = (response.headers.get("content-type") or "").lower()
                    is_json = "json" in ctype
                    if not is_json:
                        # 部分接口 Content-Type 非 json 但 body 是 json，用 URL 特征兜底
                        is_json = any(h in resp_url for h in patterns)
                    if not is_json:
                        return
                    try:
                        body = await response.body()
                    except Exception:
                        return
                    if len(body) > max_body_bytes:
                        body = body[:max_body_bytes]
                    text = body.decode("utf-8", errors="ignore")
                    if not text.strip().startswith(("{", "[")):
                        return
                    try:
                        data = json.loads(text)
                    except Exception:
                        return
                    captured.append(ApiCapture(
                        url=resp_url[:500],
                        status=response.status,
                        json=data if isinstance(data, dict) else None,
                        text_preview=text[:300],
                    ))

                page.on("response", _on_response)

                # 打开页面
                try:
                    await page.goto(url, wait_until="domcontentloaded", timeout=45000)
                except Exception as e:
                    logger.debug(f"[APIHarvester] 导航异常: {e}")
                try:
                    await page.wait_for_load_state("networkidle", timeout=12000)
                except Exception:
                    pass
                await page.wait_for_timeout(wait_ms)

                # 风控检测
                body_text = await page.evaluate("() => document.body ? document.body.innerText : ''")
                title = await page.title()
                if any(h in (body_text[:800] + title) for h in _RISK_HINTS):
                    result.risk_detected = True
                    logger.warning(f"[APIHarvester] 检测到风控特征: title={title}")

                # 滚动触发分页 API
                for _ in range(max_scrolls):
                    await page.mouse.wheel(0, 3000)
                    await page.wait_for_timeout(scroll_wait_ms)

                # 收集页面 <video>/<audio>/<source> 标签的 src（当前播放的签名直链）
                try:
                    srcs = await page.evaluate(
                        """() => Array.from(
                            document.querySelectorAll('video,audio,source')
                        ).map(el => (el.src || el.currentSrc || ''))
                          .filter(s => s && s.startsWith('http'))"""
                    )
                    for s in srcs:
                        if s not in [m["url"] for m in result.media_urls]:
                            result.media_urls.append({"url": s, "key": "video_tag", "path": "page"})
                except Exception as e:
                    logger.debug(f"[APIHarvester] video src 收集失败: {e}")

                result.page_url = page.url
            finally:
                await browser.close()

        result.api_calls = captured
        result.success = len(captured) > 0

        # 从捕获的 JSON 中提取列表数据与媒体直链
        for cap in captured:
            if cap.json:
                items = _extract_list_items(cap.json)
                if items:
                    result.items.extend(items)
                medias = _extract_media_urls(cap.json)
                if medias:
                    result.media_urls.extend(medias)

        # 去重
        result.items = _dedup_items(result.items)
        result.media_urls = _dedup_media(result.media_urls)

        if not captured:
            result.error = "未捕获到 JSON API 响应（可能被风控拦截或页面无 API）"
        logger.info(
            f"[APIHarvester] {url} → api={len(captured)}, items={len(result.items)}, "
            f"media={len(result.media_urls)}, risk={result.risk_detected}"
        )
        return result


# ==================== 启发式提取 ====================

def _extract_list_items(data: Any, max_items: int = 500) -> List[Dict[str, Any]]:
    """递归寻找 JSON 中内容最丰富的 list[dict] 数组

    评分 = 条数 × 权重：
    - 键命中容器键（items/records/aweme_list...）权重 3
    - 记录含内容字段（url/aweme_id/title/desc/video/play_addr）权重 2
    - 普通数组权重 1
    """
    best: List[Dict[str, Any]] = []
    best_score = 0.0
    best_key = ""

    _CONTENT_FIELDS = ("url", "aweme_id", "item_id", "note_id", "title", "desc",
                       "video", "play_addr", "cover", "avatar", "nickname", "name")

    def walk(node: Any, parent_key: str = "") -> None:
        nonlocal best, best_score, best_key
        if isinstance(node, dict):
            for k, v in node.items():
                key_l = str(k).lower()
                # 跳过配置类子树（abtest / sample_rate / conditional_rules 等系统数据）
                if any(h in key_l for h in ("config", "rule", "sample", "abtest",
                                            "conditional", "statistics", "setting", "monitor")):
                    continue
                if isinstance(v, list) and v and all(isinstance(i, dict) for i in v):
                    has_media = any(
                        any(f in str(rk).lower() for f in ("play_addr", "uri", "url_list", "video", "mp4"))
                        for r in v for rk in r.keys()
                    )
                    has_content = any(
                        any(f in str(rk).lower() for f in _CONTENT_FIELDS)
                        for r in v for rk in r.keys()
                    )
                    if has_media:
                        w = 10
                    elif key_l in _DATA_LIST_KEYS:
                        w = 3
                    elif has_content:
                        w = 2
                    else:
                        w = 1
                    score = len(v) * w
                    if score > best_score:
                        best = v
                        best_score = score
                        best_key = key_l
                walk(v, str(k))
        elif isinstance(node, list):
            for i in node:
                walk(i, parent_key)

    walk(data)
    # 排除纯配置类记录（js_error / abtest 等系统字段）
    filtered = []
    for r in best:
        key_l = " ".join(str(rk).lower() for rk in r.keys())
        if any(h in key_l for h in ("js_error", "abtest", "sample_rate", "enable", "conditional_sample")):
            continue
        filtered.append(r)
    if not filtered and best:
        filtered = [r for r in best if len(r) > 2 and not any(
            h in " ".join(str(rk).lower() for rk in r.keys())
            for h in ("js_error", "abtest", "sample_rate", "enable", "conditional_sample")
        )]
    return filtered[:max_items]


def _extract_media_urls(data: Any, max_items: int = 100) -> List[Dict[str, str]]:
    """递归提取媒体直链（play_addr / video / mp4 / m3u8 等）"""
    medias: List[Dict[str, str]] = []
    seen = set()

    def walk(node: Any, path: str = "") -> None:
        if len(medias) >= max_items:
            return
        if isinstance(node, dict):
            for k, v in node.items():
                key_l = str(k).lower()
                if isinstance(v, str) and v.startswith("http"):
                    if (_MEDIA_EXT_RE.search(v)
                            or any(h in key_l for h in _MEDIA_KEY_HINTS)
                            or _MEDIA_DOMAIN_RE.search(v)):
                        if v not in seen:
                            seen.add(v)
                            medias.append({"url": v, "key": str(k), "path": path})
                else:
                    walk(v, f"{path}.{k}" if path else str(k))
        elif isinstance(node, list):
            for i, item in enumerate(node):
                if isinstance(item, str) and item.startswith("http"):
                    if (_MEDIA_EXT_RE.search(item)
                            or _MEDIA_DOMAIN_RE.search(item)):
                        if item not in seen:
                            seen.add(item)
                            medias.append({"url": item, "key": "url", "path": path})
                else:
                    walk(item, f"{path}[{i}]")

    walk(data)
    return medias[:max_items]


def _dedup_items(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """按 url / id 字段去重"""
    seen = set()
    out = []
    for it in items:
        keys = []
        for k in ("url", "id", "aweme_id", "item_id", "note_id", "vid", "video_id"):
            v = it.get(k)
            if v is not None:
                keys.append(f"{k}:{v}")
        sig = "|".join(keys) if keys else json.dumps(it, ensure_ascii=False, sort_keys=True)[:200]
        if sig in seen:
            continue
        seen.add(sig)
        out.append(it)
    return out


def _dedup_media(medias: List[Dict[str, str]]) -> List[Dict[str, str]]:
    seen = set()
    out = []
    for m in medias:
        if m["url"] in seen:
            continue
        seen.add(m["url"])
        out.append(m)
    return out


def _parse_netscape_cookies(path: str) -> List[Dict[str, Any]]:
    """解析 Netscape cookies.txt → Playwright cookie 列表（供 add_cookies 注入）

    格式：domain \t includeSubdomains \t path \t secure \t expires \t name \t value
    """
    from pathlib import Path

    p = Path(path)
    if not p.exists():
        logger.warning(f"[APIHarvester] cookies.txt 不存在: {path}")
        return []
    cookies = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 7:
            continue
        domain, _sub, cpath, secure, expires, name, value = parts[:7]
        cookies.append({
            "name": name,
            "value": value.replace("%09", "\t").replace("%0A", "\n"),
            "domain": domain,
            "path": cpath or "/",
            "secure": secure.lower() == "true",
            "httpOnly": False,
            "expires": int(expires or 0),
        })
    return cookies


__all__ = ["BrowserAPIHarvester", "HarvestResult", "ApiCapture"]
