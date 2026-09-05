"""站点档案工具 — 首次抓取后保存站点策略，后续复用跳过分析。

设计意图（Site Profile 模式）：
    每个站点首次抓取时，Agent 详细分析站点结构并保存为档案（origin + strategy + notes + cookies）。
    之后再次抓取该站点，Agent 先查档案，命中则按策略直接执行，省去重复分析。
    档案以站点根 URL (origin) 为唯一键，存储在 data/profiles/{domain}.yaml（Phase 3 重构：
    从单文件 sites.json 拆成 per-domain YAML，支持 git diff + 版本管理）。

cookies 是站点的访问凭据（如微信读书的 wr_vid/wr_ssk）——凭据属于站点而非全局，
任何需要登录的站点都可以在自己的档案里存 Cookie，由对应的站点工具读取。

脚本复用设计（recommend_scripts 模式）：
    档案可额外带 kind（脚本类型标签）+ tags（自由标签），用于跨站脚本推荐。
    kind 建议枚举：
      csdn_blog / fanqie_font_crypt / weread_cookie / wallpaper_bgimg_cards /
      video_dplayer_iframe / video_m3u8_scrape / douyin_bili_share /
      js_eval_obfuscated / api_token_sign / rss_sitemap_fallback / custom
    下次遇到相似结构（但域名不同）的站，调 recommend_scripts(hint="字体混淆")
    即可拿到之前训练好的脚本做模板，不用从零再写。

Phase 3 存储格式（data/profiles/{domain}.yaml）：
    每个站点一个 YAML 文件，以 origin 的 scheme+host 安全化后命名：
      https://weread.qq.com → weread_qq_com.yaml
    字段与旧 sites.json 完全一致，额外 Phase 3 3.2 预留顶层 config 字段：
    ---
    origin: https://weread.qq.com
    title: 微信读书
    strategy: weread_cookie
    kind: weread_cookie
    tags: [vip, 登录Cookie]
    notes: 需要 Cookie 认证，使用 list_weread_chapters + get_weread_chapter
    cookies: wr_vid=xxx; wr_ssk=yyy
    script: |
      # run_custom_script saved code
    created_at: 2026-08-17T10:00:00
    last_crawled_at: 2026-08-17T10:30:00
    # Phase 3 3.2 可选：per-profile 多环境配置
    config:
      output_dir: profiles_output/weread
      model: deepseek-v3
"""
import json
import re
import threading
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import yaml
from langchain_core.tools import tool

from crawagent.config.settings import get_settings

# ---------------------------------------------------------------------------
# Phase 3 — 存储层：per-domain YAML + per-origin 锁
# ---------------------------------------------------------------------------

# 迁移标记文件：存在即表示 sites.json → profiles/*.yaml 已完成（不再重复迁移）
_MIGRATION_DONE = ".migrated_from_sites_json.ok"

# per-origin 锁字典（替代 Phase 2 的全局 _lock）：
# 不同域名的读写互不阻塞；同域名仍串行保证线程安全
_locks: dict[str, threading.Lock] = {}
_global_lock = threading.Lock()  # 仅保护 _locks 字典本身的创建


def _profiles_dir() -> Path:
    return get_settings().project_root / "data" / "profiles"


def _old_json_path() -> Path:
    return get_settings().project_root / "data" / "sites.json"


def _origin_to_filename(origin: str) -> str:
    """把 origin 安全化做 yaml 文件名。

    https://weread.qq.com → weread_qq_com.yaml
    https://www.bilibili.com → www_bilibili_com.yaml
    """
    norm = _normalize_origin(origin)
    # 去掉 scheme 前缀
    host = norm.replace("https://", "").replace("http://", "").replace("file://", "")
    # 非法字符统一替换为 _（Windows 文件名限制）
    safe = re.sub(r"[^\w\-.]", "_", host)
    # 防止 . 开头或结尾
    safe = safe.strip(".") or "unknown"
    return f"{safe}.yaml"


def _lock_for(origin: str) -> threading.Lock:
    """获取/创建 origin 对应的文件锁。"""
    with _global_lock:
        if origin not in _locks:
            _locks[origin] = threading.Lock()
        return _locks[origin]


def _load_profile(origin: str) -> dict | None:
    """读单个站点 yaml，找不到返回 None。"""
    p = _profiles_dir() / _origin_to_filename(origin)
    if not p.exists():
        return None
    try:
        text = p.read_text(encoding="utf-8")
        data = yaml.safe_load(text)
        if isinstance(data, dict) and data.get("origin"):
            return data
    except (yaml.YAMLError, OSError):
        pass
    return None


def _save_profile(profile: dict) -> None:
    """写单个站点 yaml（覆盖写）。"""
    p = _profiles_dir() / _origin_to_filename(profile["origin"])
    p.parent.mkdir(parents=True, exist_ok=True)
    text = yaml.safe_dump(
        profile,
        allow_unicode=True,
        default_flow_style=False,
        sort_keys=False,
    )
    p.write_text(text, encoding="utf-8")


def _delete_profile_file(origin: str) -> bool:
    """删除站点 yaml 文件。"""
    p = _profiles_dir() / _origin_to_filename(origin)
    if p.exists():
        p.unlink()
        return True
    return False


def _load_all_profiles() -> list[dict]:
    """扫 profiles/ 目录下所有 yaml，返回 list[dict]（按 origin 排序）。"""
    d = _profiles_dir()
    if not d.exists():
        return []
    results: list[dict] = []
    for py in sorted(d.glob("*.yaml")):
        try:
            data = yaml.safe_load(py.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("origin"):
                results.append(data)
        except (yaml.YAMLError, OSError):
            continue
    results.sort(key=lambda s: s.get("origin", ""))
    return results


# ---------------------------------------------------------------------------
# 自动迁移：旧 sites.json → 新 profiles/*.yaml
# ---------------------------------------------------------------------------

def _migrate_json_to_yaml() -> None:
    """启动时自动迁移：如果 sites.json 存在 + profiles/ 不存在且无迁移标记 → 执行迁移。

    幂等：迁移完成后写标记文件 .migrated_from_sites_json.ok，重启不会重复跑。
    旧 sites.json 保留不删（备份，用户确认没问题后可手动删）。
    """
    profiles = _profiles_dir()
    json_p = _old_json_path()
    marker = profiles / _MIGRATION_DONE

    # 条件不满足直接返回
    if marker.exists() or not json_p.exists():
        return

    try:
        import json as _json
        data = _json.loads(json_p.read_text(encoding="utf-8"))
        sites = data.get("sites", []) if isinstance(data, dict) else []

        profiles.mkdir(parents=True, exist_ok=True)
        for s in sites:
            if not s.get("origin"):
                continue
            _save_profile(s)

        # 写迁移标记
        marker.write_text(
            f"migrated {len(sites)} records at {datetime.now().isoformat(timespec='seconds')}\n",
            encoding="utf-8",
        )
        print(f"[site_profile_tool] auto-migrated {len(sites)} sites.json → profiles/*.yaml")
    except Exception as e:
        print(f"[site_profile_tool] migration failed (non-fatal): {e}")


# 迁移标记文件状态（惰性初始化）
_MIGRATION_CHECKED = False


def _ensure_migrated() -> None:
    """惰性确保 sites.json → YAML 迁移完成。

    不能在模块加载时立即调 _migrate_json_to_yaml()，因为它依赖 _origin_to_filename，
    而 _origin_to_filename 又依赖 _normalize_origin — 后者在更下面定义。
    所以改成惰性：第一个公共 API 调用时才做。
    """
    global _MIGRATION_CHECKED
    if _MIGRATION_CHECKED:
        return
    _MIGRATION_CHECKED = True
    try:
        _migrate_json_to_yaml()
    except Exception:
        pass

# ---------------------------------------------------------------------------
# kind 规范枚举（供 recommend_scripts 排序 + 做命中优先级，新类型加这里即可）
# ---------------------------------------------------------------------------

_KIND_CANON = {
    "csdn_blog", "fanqie_font_crypt", "weread_cookie",
    "wallpaper_bgimg_cards", "video_dplayer_iframe", "video_m3u8_scrape",
    "douyin_bili_share", "js_eval_obfuscated", "api_token_sign",
    "rss_sitemap_fallback", "custom",
}


def _normalize_origin(url: str) -> str:
    """从任意 URL 提取站点根（scheme + host），已带尾部斜杠的根 URL 也接受"""
    if not url:
        return ""
    # 没带 scheme 时 urlparse 会把 host 误判为 path，补一个
    if "://" not in url:
        url = "https://" + url
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return url.rstrip("/").lower()
    return f"{parsed.scheme}://{parsed.netloc}".lower()


def list_sites() -> list[dict]:
    """供 server.py 的 /api/sites 路由复用：返回全部档案。

    Phase 3: 内部改为扫描 data/profiles/*.yaml，函数签名不变。
    """
    _ensure_migrated()
    return _load_all_profiles()


def upsert_site(
    origin: str,
    title: str = "",
    strategy: str = "",
    notes: str = "",
    cookies: str | None = None,
    script: str | None = None,
    kind: str = "",
    tags: list[str] | None = None,
    config: dict | None = None,
) -> dict:
    """供 server.py 的 POST /api/sites 复用：创建或更新档案

    Phase 3: 内部改为 per-domain YAML + per-origin 锁。函数签名不变。
    cookies 为 None 时不修改；传空字符串可清除已存的 Cookie。
    script 为 None 时不修改；传空字符串可清除已存的自定义脚本。
    kind 为空串时不修改；传 "custom" 可显式设为自定义类。
    tags 为 None 时不修改；传空列表可清空标签。
    config 为 None 时不修改；传 dict 则覆盖整个 config 块。
      Phase 3.2 可选字段示例：
        {"output_dir": "output/weread", "model": "deepseek-v3", "provider": "local"}
    """
    _ensure_migrated()
    norm = _normalize_origin(origin)
    if not norm:
        raise ValueError("invalid origin")
    now = datetime.now().isoformat(timespec="seconds")

    with _lock_for(norm):
        existing = _load_profile(norm)
        if existing:
            # 更新已有档案（只改非空/非 None 字段）
            if title:
                existing["title"] = title
            if strategy:
                existing["strategy"] = strategy
            if notes:
                existing["notes"] = notes
            if cookies is not None:
                existing["cookies"] = cookies
            if script is not None:
                existing["script"] = script
            if kind:
                existing["kind"] = kind if kind in _KIND_CANON else "custom"
            if tags is not None:
                existing["tags"] = [str(t).strip() for t in tags if str(t).strip()]
            if config is not None:
                existing["config"] = config
            existing["last_crawled_at"] = now
            _save_profile(existing)
            return existing

        # 创建新档案
        new = {
            "origin": norm,
            "title": title or norm,
            "strategy": strategy,
            "kind": (kind if kind in _KIND_CANON else "custom") if kind else "",
            "tags": [str(t).strip() for t in (tags or []) if str(t).strip()],
            "notes": notes,
            "cookies": cookies or "",
            "script": script or "",
            "created_at": now,
            "last_crawled_at": now,
        }
        if config:
            new["config"] = config
        _save_profile(new)
        return new


def delete_site(origin: str) -> bool:
    """供 server.py 的 DELETE /api/sites/{origin} 复用"""
    _ensure_migrated()
    norm = _normalize_origin(origin)
    with _lock_for(norm):
        return _delete_profile_file(norm)


def get_site_cookies(origin: str) -> str:
    """读取站点档案中存的 Cookie（无档案或无 Cookie 时返回空串）。

    站点工具（如 weread_tool）用它取本站凭据——凭据留在档案文件里，
    不进入 LLM 上下文。
    """
    _ensure_migrated()
    norm = _normalize_origin(origin)
    with _lock_for(norm):
        p = _load_profile(norm)
    return (p.get("cookies", "") if p else "") or ""


def get_profile_config(origin: str) -> dict:
    """Phase 3.2 — 读取站点档案的 per-profile 多环境配置块。

    用法：Agent 主循环在开始爬某个站前调它，拿到 per-origin 覆盖项，
    与全局 settings 合并得到最终生效配置。
    返回空 dict 表示没有 profile config。

    典型 config 字段（全部可选）：
      output_dir:   文本产物子目录（相对路径基于 settings.project_root）
      downloads_dir: 媒体下载子目录
      model:        覆盖用哪个 LLM（字符串，如 "deepseek-v3"）
      provider:     服务商覆盖（如 "local"、"openrouter"）
    """
    _ensure_migrated()
    norm = _normalize_origin(origin)
    with _lock_for(norm):
        p = _load_profile(norm)
    if p is None:
        return {}
    return p.get("config") or {}


@tool
def list_site_profiles(origin: str) -> str:
    """按 origin 查询已存档的站点档案（data/sites.json）。

    每个档案记录本站的爬取策略、备注、Cookie 是否已设置、是否有训练好的脚本。
    **用户要求爬某个站时，先调它**——如果有档案，就按它的 strategy 和 notes 直接执行，
    省去重复分析站点结构。

    参数：
        origin: 用户给的站点 URL，例 "https://weread.qq.com" 或 "weread.qq.com"。
                内部会先标准化为 scheme+host 再比对。

    返回：
        命中档案：返回格式化清单，含以下字段：
          title: 人类可读站名
          strategy: 本站推荐爬取策略
          kind: 脚本类型标签（用于跨站 recommend_scripts 复用）
          tags: 自由标签串
          notes: 备忘备注
          cookies: 是否已设置登录 Cookie
          script: 已存/未存 + 脚本前 200 字预览
          last_crawled: 最近一次爬取时间
        未命中：返回 NO_PROFILE 并提示先分析，成功后再调 save_site_profile 存档。
    """
    norm = _normalize_origin(origin)
    p = _load_profile(norm)
    if p:
        script_set = bool(p.get("script"))
        script_info = "set" if script_set else "not_set"
        script_preview = ""
        if script_set:
            preview = (p["script"] or "")[:200]
            script_preview = f"\nscript_preview: {preview}{'...' if len(p['script']) > 200 else ''}"
        tags = p.get("tags") or []
        return (
            f"FOUND: {norm}\n"
            f"title: {p.get('title', '')}\n"
            f"strategy: {p.get('strategy', '')}\n"
            f"kind: {p.get('kind', '')}\n"
            f"tags: {', '.join(tags) if tags else '-'}\n"
            f"notes: {p.get('notes', '')}\n"
            f"cookies: {'set' if p.get('cookies') else 'not_set'}\n"
            f"script: {script_info}{script_preview}\n"
            f"last_crawled: {p.get('last_crawled_at', '')}"
        )
    return (
        f"NO_PROFILE: {norm} — analyze the site structure first, "
        "and after one successful crawl call save_site_profile to archive it for reuse."
    )


@tool
def save_site_profile(
    origin: str,
    title: str,
    strategy: str,
    notes: str = "",
    cookies: str = "",
    script: str = "",
    kind: str = "",
    tags: str = "",
) -> str:
    """成功爬完一个站后：保存或更新它的站点档案（data/sites.json）。

    **成功拿下某个站、摸清楚有效策略后立刻调它**——下次再访问同一个站
    可以直接跳过分析阶段复用档案。同时它也是**保存登录 Cookie 的唯一入口**：
    任何需要 Cookie 鉴权的站点（如微信读书 wr_vid/wr_ssk）都把 Cookie 字串
    存在对应的站点档案里——用户给了你 Cookie 后必须用它保存，
    这样 Cookie 不会污染聊天上下文，工具会自己从档案读取。

    如果之前写了 run_custom_script 才搞定这个站，一定要把脚本源码存在
    `script` 参数里，下次遇到同类结构（同 kind）的站，`recommend_scripts`
    会把它当模板推出来。

    参数：
        origin: 站点 URL，如 "https://weread.qq.com" 或 "weread.qq.com"
        title: 人类可读的站点名，如 "微信读书"、"番茄小说"
        strategy: 成功的爬取策略，建议枚举之一：
                  "crawl_webpage"（纯静态 HTML，不用渲染）
                  "browse_and_crawl"（SPA / JS 渲染 / 字体加密）
                  "weread_cookie"（微信读书，要 Cookie 鉴权）
                  "wallpaper"（壁纸/图片站，CSS 背景图卡片）
                  "social_media"（抖音/B 站作品下载）
                  "custom_script"（必须写自定义 Python 脚本）
                  或一条简短自定义描述（若以上都不匹配）
        notes: 备忘注意事项，例 "必须登录 Cookie"、"章节字体混淆"、
               "VIP 章节需要走代理接口"
        cookies: 登录 Cookie 字串，例 "wr_vid=xxx; wr_ssk=yyy"；
                 **只在用户主动提供时才填**。仅存站点档案，绝不回显到聊天。
        script: 成功爬取本站的自定义 Python 脚本源码。保存后下次直接拿来跑。
        kind: 【可选】脚本类型标签，用于跨站复用推荐。建议枚举（新类型会归为 custom）：
              csdn_blog / fanqie_font_crypt / weread_cookie / wallpaper_bgimg_cards /
              video_dplayer_iframe / video_m3u8_scrape / douyin_bili_share /
              js_eval_obfuscated / api_token_sign / rss_sitemap_fallback / custom
        tags: 【可选】自由标签，用英文逗号分隔，例 "字体混淆,VIP,CSDN"。供 recommend_scripts 的 hint 搜索。

    返回：
        成功："已保存: {origin} ({strategy})"
    """
    try:
        tag_list = [t.strip() for t in tags.replace("，", ",").split(",") if t.strip()] if tags else None
        rec = upsert_site(
            origin,
            title=title,
            strategy=strategy,
            notes=notes,
            cookies=cookies or None,
            script=script or None,
            kind=kind or "",
            tags=tag_list,
        )
        return f"SAVED: {rec['origin']} ({rec.get('strategy', strategy)})"
    except Exception as e:
        return f"ERROR saving site profile: {e}"


@tool
def recommend_scripts(url: str = "", hint: str = "", limit: int = 3) -> str:
    """查历史站点档案，推荐可复用的脚本/策略模板（跨站也能用）。

    **非强制使用。** 当你感觉本站结构和之前爬过的某个站像、或脚本试了几条都卡住、
    或用户说一句"之前那个爬 XX 的脚本套一下这个站"——这时候再调它。
    正常流程（有本站自己的 profile 或简单爬成功）**不要调**，避免上下文变繁琐。

    匹配优先级（不搞向量，纯字符串快且准，不会给你假相似度）：
      1) 如果传了 url：先从它推导 kind（域名/路径特征规则），再查同 kind 的档案
      2) 如果传了 hint（推荐）：在 kind / tags / title / notes / strategy 里做大小写不敏感子串匹配
      3) 最后兜底：带 saved_script 的档案按 last_crawled_at 倒序取最近几条

    参数：
        url: 【可选】当前目标站 URL，传了就会尝试做 kind 推断再匹配
        hint: 【推荐】关键词提示，例 "字体混淆" / "CSDN" / "壁纸 bgimg" /
              "微信读书" / "m3u8 DPlayer" / "JS eval 混淆"。中英混用都行。
        limit: 最多返回几条（默认 3，避免喂给 AI 太多参考上下文）

    返回：
        推荐列表，每条：rank / origin / title / kind / tags / strategy /
        script_preview（脚本前 200 字模板预览）；一条都没命中时返回 "NO_RECOMMEND"
    """
    _ensure_migrated()
    sites = _load_all_profiles()
    if not sites:
        return "NO_RECOMMEND: 档案库为空，存几个 profile 以后就会有推荐了。"

    limit = min(max(limit, 1), 5)
    scored: list[tuple[int, dict]] = []

    # hint 分词：中英文分隔符统一
    import re
    tokens = [t.strip().lower() for t in re.split(r"[ ,，、]+", hint or "") if t.strip()]

    # URL 推 kind
    inferred_kind = ""
    if url:
        host = _normalize_origin(url).replace("https://", "").replace("http://", "")
        lh = host.lower()
        if "weread.qq.com" in lh or "微信读书" in host:
            inferred_kind = "weread_cookie"
        elif "fanqie" in lh or "novel.sfanqie" in lh or "jjnovel" in lh or "ttnovel" in lh:
            inferred_kind = "fanqie_font_crypt"
        elif "csdn" in lh:
            inferred_kind = "csdn_blog"
        elif "douyin.com" in lh or "bilibili.com" in lh or "b23.tv" in lh:
            inferred_kind = "douyin_bili_share"

    for s in sites:
        score = 0
        skind = s.get("kind") or ""
        stags = s.get("tags") or []
        s_text = " ".join(
            [
                s.get("title", ""),
                s.get("strategy", ""),
                s.get("notes", ""),
                skind,
                " ".join(stags),
            ]
        ).lower()

        # 1) url 推 kind 命中：权重最高
        if inferred_kind and skind == inferred_kind:
            score += 100
        # 2) hint tokens 命中 kind/tags/title/notes/strategy：每命中 1 词 +20
        for tok in tokens:
            if not tok:
                continue
            if skind == tok or tok == skind:
                score += 40
            if any(tok == tag.lower() for tag in stags):
                score += 30
            if tok in s_text:
                score += 20
        # 3) 带 saved_script 加分（我们要的是可复用脚本，不是空档案）
        if s.get("script"):
            score += 10
        # 4) 最近爬过的兜底 +1 分/近 7 天（对无 hint 空搜情况给出最近经验）
        try:
            last = datetime.fromisoformat(s.get("last_crawled_at") or "")
            days = max(0, (datetime.now() - last).days)
            score += max(0, 8 - days)  # 近 8 天越新分越高
        except Exception:
            pass

        if score > 0 or (not url and not hint and s.get("script")):
            scored.append((score, s))

    if not scored:
        return "NO_RECOMMEND: 没有匹配到可复用脚本。换个 hint 关键词，或者先存几个带 script 的 profile。"

    # 分数倒序、同分时 last_crawled_at 新的在前
    scored.sort(key=lambda x: (x[0], x[1].get("last_crawled_at", "")), reverse=True)
    picked = scored[:limit]

    lines = [f"RECOMMEND: {len(picked)} 条，优先按评分最高 → 最近爬过"]
    for i, (sc, s) in enumerate(picked, 1):
        tags = s.get("tags") or []
        script_preview = ""
        if s.get("script"):
            p = s["script"][:200]
            script_preview = f"\n  script_preview: {p}{'...' if len(s['script']) > 200 else ''}"
        lines.append(
            f"[{i}] score={sc} kind={s.get('kind','-')} origin={s.get('origin','')}\n"
            f"  title: {s.get('title','')}\n"
            f"  strategy: {s.get('strategy','')}\n"
            f"  tags: {', '.join(tags) if tags else '-'}\n"
            f"  last_crawled: {s.get('last_crawled_at','')}{script_preview}"
        )
    return "\n".join(lines)
