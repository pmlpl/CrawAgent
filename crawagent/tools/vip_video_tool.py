"""VIP 视频解析工具 — 遍历第三方解析接口，找到可播放的 VIP 视频地址。

与 VipVideo（Electron 应用）同源的机制：URL 拼接 `解析接口前缀 + 视频页URL` →
第三方解析服务返回播放器页面。本工具自身不做任何破解，只做接口的并发预筛
（prescreen）与成功率缓存，复用 VipVideo test-parsers.js 验证过的判定正则。

工具清单（2 个）：
  parse_vip_video     — 并发预筛 40+ 解析接口，返回第一个（成功率最高的）可播放地址
  list_vip_platforms  — 列出支持的视频平台 + canvip 标记
"""
from __future__ import annotations

import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from langchain_core.tools import tool

from crawagent.config.settings import get_settings
from crawagent.tools.android_reverse_tool import _truncate
from crawagent.tools.social_utils import DESKTOP_UA

# vlist.json 随包资源（45 解析接口 + 32 平台，源自 VipVideo app.asar）
_ASSETS_DIR = Path(__file__).resolve().parent / "assets"
_VLIST_PATH = _ASSETS_DIR / "vlist.json"

# 解析接口成功率缓存（data/parser_cache.json）
_CACHE_NAME = "parser_cache.json"

# ---- 预筛判定正则（复用 VipVideo test-parsers.js 验证过的规则）----
# 视频流 URL 特征：.m3u8 / .mp4 / .ts / .flv / .m4s / .mkv / .webm / .mov
_STREAM_URL_RE = re.compile(
    r"\.(m3u8|mp4|ts|flv|mkv|m4s|webm|mov)(\?|#|$)", re.IGNORECASE
)
# 播放器标记：<video> / <iframe> / player 脚本
_PLAYER_MARK_RE = re.compile(r"<video|<iframe|player", re.IGNORECASE)
# 失败文本：解析服务明确表示解析不了
_FAIL_TEXT_RE = re.compile(
    r"(解析失败|不支持该|线路维护|资源不存在|该网站暂不支持|视频已下线"
    r"|parameter error|url error)",
    re.IGNORECASE,
)
# 视频流类型名（用于输出展示）
_STREAM_TYPES = {"m3u8": "m3u8（HLS）", "mp4": "mp4（直链）", "flv": "flv"}


def _load_vlist() -> dict | None:
    """加载 vlist.json；缺失/损坏返回 None。"""
    try:
        return json.loads(_VLIST_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None


def _cache_path() -> Path:
    return get_settings().project_root / "data" / _CACHE_NAME


def _load_cache() -> dict:
    """读解析接口成功率缓存；无缓存/损坏返回空 dict。"""
    try:
        return json.loads(_cache_path().read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_cache(cache: dict) -> None:
    """写缓存；失败静默（缓存是优化项，不影响主流程）。"""
    try:
        p = _cache_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception:
        pass


def _success_rate(entry: dict) -> float:
    s = entry.get("success_count", 0)
    f = entry.get("fail_count", 0)
    total = s + f
    return s / total if total else 0.0


def _prescreen_one(parser_url: str, video_url: str, timeout: int = 5) -> dict:
    """对单个解析接口发 GET {parser_url}{video_url}，返回判定结果。

    判活：HTTP < 400 且（含视频流 URL 或 播放器标记）且 不含失败文本。
    判死：不可达 / >=400 / 响应过短 / 含失败文本。
    """
    import requests

    target = f"{parser_url}{video_url}"
    try:
        resp = requests.get(
            target,
            headers={"User-Agent": DESKTOP_UA},
            timeout=timeout,
            allow_redirects=True,
        )
    except Exception as e:
        return {"status": "FAIL", "reason": f"网络失败: {type(e).__name__}", "body": ""}
    if resp.status_code >= 400:
        return {"status": "FAIL", "reason": f"HTTP {resp.status_code}", "body": ""}
    body = resp.text or ""
    if _FAIL_TEXT_RE.search(body):
        return {"status": "FAIL", "reason": "解析服务返回失败文本", "body": body}
    stream_hit = _STREAM_URL_RE.search(body)
    player_hit = _PLAYER_MARK_RE.search(body)
    if stream_hit or player_hit:
        stype = stream_hit.group(1).lower() if stream_hit else "player"
        return {"status": "PASS", "reason": "", "body": body, "stream_type": stype,
                "final_url": resp.url}
    if len(body) < 200:
        return {"status": "FAIL", "reason": f"响应过短({len(body)}B)无播放器标记", "body": body}
    return {"status": "UNKNOWN", "reason": "有响应但未检测到播放器标记", "body": body}


def _detect_stream_type(url: str) -> str:
    m = _STREAM_URL_RE.search(url)
    t = m.group(1).lower() if m else ""
    return _STREAM_TYPES.get(t, t or "未知（播放器页）")


# ---------------------------------------------------------------------------
# 工具 1: parse_vip_video
# ---------------------------------------------------------------------------

@tool
def parse_vip_video(video_url: str, parser_name: str = "") -> str:
    """Try 40+ third-party parser APIs to resolve a VIP video page into a playable URL.

    Args:
        video_url: VIP video page URL (e.g. https://v.qq.com/x/cover/xxx.html).
        parser_name: optional parser name (e.g. "默认A"); empty = try all.

    Parsers are screened CONCURRENTLY (HTTP prescreen, ~5s timeout each), then the
    passing ones are ranked by cached success rate — the best one is returned.
    Parser usage stats are cached to data/parser_cache.json so repeated calls
    get faster and more accurate. On total failure a summary with per-parser
    reasons is returned. Pairs with download_social_media for download.
    """
    vlist = _load_vlist()
    if vlist is None:
        return f"ERR: 内置解析接口列表缺失或损坏（{_VLIST_PATH.name}），随包资源被破坏"
    parsers = vlist.get("list") or []
    if not parsers:
        return "ERR: vlist.json 中解析接口列表为空"

    # 指定接口 → 只试一个
    if parser_name:
        target = next(
            (p for p in parsers if p.get("name") == parser_name), None
        )
        if target is None:
            known = "、".join(p.get("name", "?") for p in parsers[:10])
            return f"ERR: 未找到解析接口 {parser_name!r}（可用：{known}...）"
        parsers = [target]

    # 并发预筛全部接口
    cache = _load_cache()
    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=min(12, len(parsers))) as pool:
        futures = {
            pool.submit(
                _prescreen_one, p.get("url", ""), video_url
            ): p
            for p in parsers
            if p.get("url")
        }
        for fut in as_completed(futures):
            p = futures[fut]
            r = fut.result()
            results.append({"name": p.get("name", "?"), "url": p.get("url", ""), **r})

    # 更新缓存计数
    for r in results:
        entry = cache.setdefault(r["name"], {"success_count": 0, "fail_count": 0,
                                             "last_success_ts": 0})
        if r["status"] == "PASS":
            entry["success_count"] += 1
            entry["last_success_ts"] = int(time.time())
        else:
            entry["fail_count"] += 1
    _save_cache(cache)

    # 按状态 + 成功率排序：PASS 优先，成功率高的在前
    def _rank(r: dict):
        e = cache.get(r["name"], {})
        return (0 if r["status"] == "PASS" else 1 if r["status"] == "UNKNOWN" else 2,
                -_success_rate(e), -e.get("last_success_ts", 0))

    results.sort(key=_rank)
    passed = [r for r in results if r["status"] == "PASS"]

    if not passed:
        tried = "、".join(f"{r['name']}({r['reason'] or r['status']})" for r in results[:15])
        more = f" ...等 {len(results)} 个" if len(results) > 15 else ""
        return (
            f"❌ 全部 {len(results)} 个解析接口均预筛失败。\n"
            f"视频页：{video_url}\n"
            f"尝试详情：{tried}{more}\n"
            f"常见原因：视频页 URL 无效 / 解析接口临时失效（可稍后重试或指定 parser_name 单试）/ 需要登录。"
        )

    best = passed[0]
    play_url = f"{best['url']}{video_url}"
    lines = [
        f"✅ 解析成功（接口：{best['name']}，{len(passed)}/{len(results)} 个接口预筛通过）",
        f"视频页：{video_url}",
        f"播放地址：{play_url}",
        f"流类型：{_detect_stream_type(best.get('final_url', '') or best.get('body', ''))}",
    ]
    if best.get("final_url") and best["final_url"] != play_url:
        lines.append(f"实际跳转：{best['final_url']}")
    if len(passed) > 1:
        alts = "、".join(r["name"] for r in passed[1:6])
        lines.append(f"备用接口：{alts}")
    lines.append("----")
    lines.append("响应片段：" + _truncate(best.get("body", "").strip(), 500))
    lines.append("如需下载：把播放地址交给 download_social_media；"
                 "若为播放器页可先用 browse_webpage 确认可播。")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 工具 2: list_vip_platforms
# ---------------------------------------------------------------------------

@tool
def list_vip_platforms(category: str = "all") -> str:
    """List supported video platforms and whether VIP bypass is available (canvip).

    Args:
        category: all / tv / video / music / novel / comic / other.

    Call this BEFORE parse_vip_video to confirm the platform is supported and
    guide the user to provide a valid video page URL.
    """
    vlist = _load_vlist()
    if vlist is None:
        return f"ERR: 内置平台列表缺失或损坏（{_VLIST_PATH.name}）"
    platforms = vlist.get("platformlist") or []
    if category and category != "all":
        platforms = [p for p in platforms if p.get("category") == category]
    if not platforms:
        return f"NO_PLATFORM: category={category!r} 无匹配平台"
    lines = [
        f"{'VIP' if p.get('canvip') else '   '} | {p.get('name', '?')} | {p.get('url', '')}"
        for p in platforms
    ]
    vip_count = sum(1 for p in platforms if p.get("canvip"))
    return f"共 {len(platforms)} 个平台（{vip_count} 个支持 VIP 解析），格式: VIP|名称|首页:\n" + "\n".join(lines)
