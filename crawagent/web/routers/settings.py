"""设置相关 API：多服务商模型注册表的增删改查、思考深度与连通性测试。

数据存储在 .env 的 LLM_PROVIDERS 键（JSON 数组），结构见 crawagent/llm/registry.py。
Key 按服务商共享：同一服务商下的多个模型共用一个 api_key。
"""
from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import FileResponse

from crawagent.config.settings import get_settings
from crawagent.llm.registry import load_providers
from crawagent.web.state import reset_agent_cache

router = APIRouter()

ENV_FILE = Path(__file__).resolve().parents[3] / ".env"


def _read_env_lines() -> list[str]:
    """读取 .env 原始行（保留注释与空行），用于改写时尽量不破坏格式"""
    if not ENV_FILE.exists():
        return []
    return ENV_FILE.read_text(encoding="utf-8").splitlines()


def _save_env_updates(updates: dict[str, str]) -> None:
    """把 updates 写回 .env：保留注释与其他键，更新已存在的键、追加新键"""
    lines = _read_env_lines()
    remaining = dict(updates)
    out: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            out.append(line)
            continue
        key = stripped.split("=", 1)[0].strip()
        if key in remaining:
            out.append(f"{key}={remaining.pop(key)}")
        else:
            out.append(line)
    for key, value in remaining.items():
        out.append(f"{key}={value}")
    ENV_FILE.write_text("\n".join(out) + "\n", encoding="utf-8")


def _persist_providers(providers: list[dict]) -> None:
    """把注册表写回 .env 并失效 Agent 缓存（下次对话按新配置重建）"""
    _save_env_updates({"LLM_PROVIDERS": json.dumps(providers, ensure_ascii=False)})
    # load_providers() 直接从磁盘读 .env（绕过 pydantic-settings 的 cached_property 缓存）
    # 所以写完 .env 它立即读到新值，无需额外清缓存
    reset_agent_cache()


def _find_provider(providers: list[dict], name: str) -> dict | None:
    for p in providers:
        if p.get("name") == name:
            return p
    return None


def _model_exists(providers: list[dict], model: str, exclude_provider: str = "") -> bool:
    """模型 ID 全局唯一（避免对话页选择时无法区分服务商）"""
    for p in providers:
        if p.get("name") == exclude_provider:
            continue
        if model in (p.get("models") or []):
            return True
    return False


def _settings_snapshot() -> dict[str, Any]:
    """GET /api/settings 响应：模型条目列表 + 服务商（含是否已配 Key），不含明文 Key"""
    s = get_settings()
    providers = load_providers()
    return {
        "models": [
            {"name": m, "provider": p.get("name", "")}
            for p in providers
            for m in (p.get("models") or [])
        ],
        "providers": [
            {"name": p.get("name", ""), "base_url": p.get("base_url", ""), "key_set": bool(p.get("api_key"))}
            for p in providers
        ],
        "thinking_depth": s.thinking_depth,
        "start_browser": s.start_browser,
        # 内置 anything-analyzer 检测状态（前端据此决定 UI：显示内置状态还是 textarea）
        "aa_builtin_found": _aa_status()["found"],
        "aa_builtin_path": _aa_status()["path"],
        "aa_builtin_port": _aa_status()["port"],
        # ---- 高级页（T2）：抓取参数 / 产物目录 / 保留清理策略 ----
        "request_timeout": s.request_timeout,
        "request_delay": s.request_delay,
        "output_dir": str(s.output_dir),
        "downloads_dir": str(s.downloads_dir),
        "logs_retention_days": s.logs_retention_days,
        "output_retention_days": s.output_retention_days,
        "downloads_retention_days": s.downloads_retention_days,
        "output_max_size_gb": s.output_max_size_gb,
        "downloads_max_size_gb": s.downloads_max_size_gb,
    }


def _aa_status() -> dict:
    """检测 anything-analyzer 是否可被内置启动。"""
    from crawagent.graph.skills import _find_anything_analyzer, _build_autostart_command
    s = get_settings()
    try:
        first = json.loads(s.mcp_servers)[0] if s.mcp_servers.strip() else {}
        url = first.get("url", "")
        from urllib.parse import urlparse
        port = urlparse(url).port if url else 0
    except (json.JSONDecodeError, ValueError, IndexError):
        port = 0
    aa_path = _find_anything_analyzer()
    return {
        "found": bool(aa_path),
        "path": aa_path or "",
        "port": port or 0,
    }


@router.get("/api/settings")
async def get_settings_route() -> dict[str, Any]:
    """返回模型注册表与思考深度（API Key 不出后端，前端只看 key_set）"""
    return _settings_snapshot()


def _validate(payload: dict, providers: list[dict], *, exclude_provider: str = "") -> tuple[str, str, str, str] | str:
    """校验添加/编辑请求，返回 (provider, model, api_key, base_url) 或错误信息字符串"""
    provider = str(payload.get("provider") or "").strip()
    model = str(payload.get("model") or "").strip()
    api_key = str(payload.get("api_key") or "").strip()
    base_url = str(payload.get("base_url") or "").strip()

    if not provider:
        return "服务商不能为空"
    if not model:
        return "模型 ID 不能为空"
    if _model_exists(providers, model, exclude_provider=exclude_provider):
        return f"模型 {model} 已存在（同一模型 ID 不能重复添加）"

    existing = _find_provider(providers, provider)
    if base_url and not base_url.startswith("http"):
        return "Base URL 必须以 http 开头"
    if existing is None:
        # 新服务商：必须提供 base_url 与 api_key
        if not base_url:
            return f"服务商 {provider} 需要填写 Base URL"
        if not api_key:
            return f"服务商 {provider} 需要填写 API Key"
    else:
        base_url = base_url or existing.get("base_url", "")
    return provider, model, api_key, base_url


@router.post("/api/models")
async def add_model_route(payload: dict = Body(...)) -> dict[str, Any]:
    """添加模型：新服务商必须带 api_key + base_url；已有服务商的 api_key 可留空复用"""
    providers = load_providers()
    result = _validate(payload, providers)
    if isinstance(result, str):
        return {"ok": False, "error": result}
    provider, model, api_key, base_url = result

    entry = _find_provider(providers, provider)
    if entry is None:
        providers.append({"name": provider, "base_url": base_url, "api_key": api_key, "models": [model]})
    else:
        entry.setdefault("models", []).append(model)

    _persist_providers(providers)
    return {"ok": True, **_settings_snapshot()}


@router.post("/api/models/update")
async def update_model_route(payload: dict = Body(...)) -> dict[str, Any]:
    """编辑模型：密钥框留空 = 保留目标服务商原 Key（清空重填语义）"""
    providers = load_providers()
    orig_provider = str(payload.get("orig_provider") or "").strip()
    orig_name = str(payload.get("orig_name") or "").strip()
    if not orig_name:
        return {"ok": False, "error": "缺少模型 ID"}

    # 在深拷贝上摘除原条目（拿到原服务商旧 Key 备用）；校验失败不影响已存数据
    kept: list[dict] = json.loads(json.dumps(providers, ensure_ascii=False))
    old_key = ""
    for p in kept:
        models = p.get("models") or []
        if p.get("name") == orig_provider and orig_name in models:
            old_key = p.get("api_key", "")
            p["models"] = [m for m in models if m != orig_name]
    # 原服务商若已无模型则丢弃条目（换回时 Key 由 old_key 兜底）
    kept = [p for p in kept if (p.get("models") or []) or p.get("name") != orig_provider]
    result = _validate(payload, kept)
    if isinstance(result, str):
        return {"ok": False, "error": result}
    provider, model, api_key, base_url = result

    entry = _find_provider(kept, provider)
    if entry is None:
        # 换到新服务商且没填 Key → 沿用原服务商的旧 Key
        kept.append({
            "name": provider, "base_url": base_url,
            "api_key": api_key or old_key, "models": [model],
        })
    else:
        entry.setdefault("models", []).append(model)
        if api_key:
            entry["api_key"] = api_key
        if base_url:
            entry["base_url"] = base_url

    _persist_providers(kept)
    return {"ok": True, **_settings_snapshot()}


@router.post("/api/models/delete")
async def delete_model_route(payload: dict = Body(...)) -> dict[str, Any]:
    """删除模型；服务商下模型清空时保留服务商条目（Key 留着，重新添加时无需再填）"""
    providers = load_providers()
    provider = str(payload.get("provider") or "").strip()
    name = str(payload.get("name") or "").strip()
    entry = _find_provider(providers, provider)
    if entry is None or name not in (entry.get("models") or []):
        return {"ok": False, "error": "模型不存在"}
    entry["models"] = [m for m in entry["models"] if m != name]
    _persist_providers(providers)
    return {"ok": True, **_settings_snapshot()}


def _parse_opt_number(v: Any, *, kind: type, lo: float, hi: float, label: str) -> tuple[Any, str]:
    """可选数值解析：None/'' → None（不限制）；否则校验类型与范围。返回 (value, error)。"""
    if v is None or v == "":
        return None, ""
    try:
        n = kind(v)
    except (TypeError, ValueError):
        return None, f"{label} 必须是数字"
    if not lo <= n <= hi:
        return None, f"{label} 需在 {lo} - {hi} 之间"
    return n, ""


@router.post("/api/settings")
async def post_settings_route(payload: dict = Body(...)) -> dict[str, Any]:
    """保存思考深度 / 启动浏览器 / 抓取参数 / 保留清理策略到 .env（MCP 自动启动已废除）"""
    updates: dict[str, str] = {}
    depth = payload.get("thinking_depth")
    if isinstance(depth, str) and depth.strip():
        updates["THINKING_DEPTH"] = depth.strip()

    browser = payload.get("start_browser")
    if browser is not None:
        val = str(browser).strip().lower()
        if val not in ("", "chrome", "msedge", "firefox"):
            return {"ok": False, "error": f"不支持的浏览器: {val}（可选：chrome / msedge / firefox）"}
        updates["START_BROWSER"] = val


    # ---- 高级页：产物/下载目录（解析为绝对路径并当场创建；工具层每次调用现读配置，保存即生效）----
    for key, env, label in (
        ("output_dir", "OUTPUT_DIR", "文本产物目录"),
        ("downloads_dir", "DOWNLOADS_DIR", "媒体下载目录"),
    ):
        if key not in payload:
            continue
        raw = str(payload.get(key) or "").strip().strip('"').strip("'")
        if not raw:
            return {"ok": False, "error": f"{label}不能为空"}
        p = Path(raw)
        if not p.is_absolute():
            p = Path(__file__).resolve().parents[3] / p
        try:
            p.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            return {"ok": False, "error": f"{label}创建失败：{e}"}
        updates[env] = str(p)

    # ---- 高级页（T2）：抓取参数 + 保留清理策略，统一数值表 ----
    # nullable=True 的字段 None → 落盘 "null"（= 不清理/不设上限，见 Settings.env_parse_none_str）；
    # 必填字段（超时/间隔）清空直接拒绝，避免落盘 "None" 炸掉下次启动的配置解析
    _ADV_NUMERIC = (
        ("request_timeout", "REQUEST_TIMEOUT", int, 5, 300, "抓取超时（秒）", False),
        ("request_delay", "REQUEST_DELAY", float, 0, 60, "请求间隔（秒）", False),
        ("logs_retention_days", "LOGS_RETENTION_DAYS", int, 1, 3650, "日志保留天数", True),
        ("output_retention_days", "OUTPUT_RETENTION_DAYS", int, 1, 3650, "产物保留天数", True),
        ("downloads_retention_days", "DOWNLOADS_RETENTION_DAYS", int, 1, 3650, "下载保留天数", True),
        ("output_max_size_gb", "OUTPUT_MAX_SIZE_GB", float, 0.1, 1024, "产物容量上限（GB）", True),
        ("downloads_max_size_gb", "DOWNLOADS_MAX_SIZE_GB", float, 0.1, 1024, "下载容量上限（GB）", True),
    )
    for key, env, kind, lo, hi, label, nullable in _ADV_NUMERIC:
        if key not in payload:
            continue
        v, err = _parse_opt_number(payload[key], kind=kind, lo=lo, hi=hi, label=label)
        if err:
            return {"ok": False, "error": err}
        if v is None:
            if not nullable:
                return {"ok": False, "error": f"{label}不能为空"}
            updates[env] = "null"
        else:
            updates[env] = str(v)

    if updates:
        _save_env_updates(updates)

    return {
        "ok": True, "saved": list(updates.keys()),
        "thinking_depth": get_settings().thinking_depth,
        "start_browser": get_settings().start_browser,
    }


@router.post("/api/settings/test")
async def test_settings_route(payload: dict = Body(...)) -> dict[str, Any]:
    """用给定配置做一次 ping 连通性测试（不影响当前 Agent）。

    api_key 留空时按服务商取已存 Key；base_url 留空时同上。
    """
    try:
        from langchain_core.messages import HumanMessage
        from langchain_openai import ChatOpenAI

        provider = str(payload.get("provider") or "").strip()
        model = str(payload.get("model") or "").strip()
        base_url = str(payload.get("base_url") or "").strip()
        api_key = str(payload.get("api_key") or "").strip()

        entry = _find_provider(load_providers(), provider) if provider else None
        if entry:
            base_url = base_url or entry.get("base_url", "")
            api_key = api_key or entry.get("api_key", "")
        if not model:
            return {"ok": False, "error": "缺少模型 ID"}
        if not api_key or "*" in api_key:
            return {"ok": False, "error": "该服务商还没有配置 API Key"}
        if not base_url:
            return {"ok": False, "error": "缺少 Base URL"}

        llm = ChatOpenAI(
            api_key=api_key,
            base_url=base_url,
            model=model,
            temperature=0,
            timeout=30,
            max_retries=0,
        )

        def _invoke() -> str:
            result = llm.invoke([HumanMessage(content="ping")])
            return result.content if isinstance(result.content, str) else str(result.content)

        content = await asyncio.to_thread(_invoke)
        return {"ok": True, "response": (content or "").strip()[:80] or "（空响应）"}
    except Exception as e:
        return {"ok": False, "error": str(e) or e.__class__.__name__}


def _mask_mcp_headers(servers: list[dict]) -> list[dict]:
    """MCP server 配置出前端前的鉴权头脱敏：值只留前 10 字符 + ***。

    历史教训（Key 覆盖 Bug）：回写时含 * 的值视为掩码、还原为已存原值，
    绝不把掩码写回 .env（见 _unmask_mcp_headers）。
    """
    out: list[dict] = []
    for srv in servers:
        s2 = {k: v for k, v in srv.items() if k != "headers"}
        headers = srv.get("headers") or {}
        if isinstance(headers, dict) and headers:
            s2["headers"] = {
                k: (str(v)[:10] + "***") if len(str(v)) > 12 else str(v)
                for k, v in headers.items()
            }
        out.append(s2)
    return out


def _unmask_mcp_headers(incoming: list[dict], current: list[dict]) -> list[dict]:
    """回写还原：头值含 *** 的视为掩码，用当前 .env 里的原值顶回（按 name+header key 对位）。"""
    cur_by_name = {str(s.get("name")): s for s in current if isinstance(s, dict)}
    out: list[dict] = []
    for srv in incoming:
        s2 = dict(srv)
        headers = srv.get("headers")
        if isinstance(headers, dict) and headers:
            fixed = dict(headers)
            old = (cur_by_name.get(str(srv.get("name"))) or {}).get("headers") or {}
            for k, v in fixed.items():
                if isinstance(v, str) and "***" in v and isinstance(old.get(k), str):
                    fixed[k] = old[k]
            s2["headers"] = fixed
        out.append(s2)
    return out


@router.get("/api/ecosystem")
async def get_ecosystem_route() -> dict[str, Any]:
    """生态面板快照：技能索引 / MCP server 列表（脱敏+状态）/ 已装插件。"""
    from crawagent.graph.skills import load_skill_index, mcp_servers_status
    from crawagent.tools.registry import list_plugins

    s = get_settings()
    try:
        raw_servers = json.loads(s.mcp_servers) if s.mcp_servers.strip() else []
    except json.JSONDecodeError:
        raw_servers = []

    skills = []
    for item in load_skill_index():
        dir_norm = str(item["dir"]).replace("\\", "/")
        is_plugin = "/plugins/" in dir_norm and "/skills/" in dir_norm
        skills.append({
            "name": item["name"],
            "description": item["description"],
            "source": "plugin" if is_plugin else "builtin",
        })

    return {
        "skills": skills,
        "skills_dirs": s.skills_dirs,
        "mcp_servers": _mask_mcp_headers(raw_servers),
        "mcp_status": mcp_servers_status(),
        "plugins": list_plugins(),
    }


@router.post("/api/mcp/servers/save")
async def save_mcp_servers_route(payload: dict = Body(...)) -> dict[str, Any]:
    """整表保存 MCP server 列表（设置页生态面板：开关/增删/编辑）。

    - 含 *** 的 header 值视为脱敏掩码，还原为 .env 里的原值
    - 保存后清 settings/MCP/Agent 三层缓存，并在后台预热一次握手
      （stdio server 冷启动需数秒，前端保存按钮期间完成）
    """
    incoming = payload.get("servers")
    if not isinstance(incoming, list):
        return {"ok": False, "error": "servers 必须是数组"}

    s = get_settings()
    try:
        current = json.loads(s.mcp_servers) if s.mcp_servers.strip() else []
    except json.JSONDecodeError:
        current = []

    cleaned: list[dict] = []
    seen_names: set[str] = set()
    for srv in incoming:
        if not isinstance(srv, dict):
            continue
        name = str(srv.get("name") or "").strip()
        if not name:
            return {"ok": False, "error": "每个 server 都需要 name"}
        if name in seen_names:
            return {"ok": False, "error": f"server 名重复: {name}"}
        seen_names.add(name)
        entry = {
            "name": name,
            "transport": str(srv.get("transport") or "stdio"),
            "disabled": bool(srv.get("disabled")),
        }
        if entry["transport"] == "stdio":
            cmd = str(srv.get("command") or "").strip()
            if not cmd:
                return {"ok": False, "error": f"stdio server '{name}' 缺 command"}
            entry["command"] = cmd
            args = srv.get("args")
            if isinstance(args, list) and args:
                entry["args"] = [str(a) for a in args]
        else:
            url = str(srv.get("url") or "").strip()
            if not url.startswith(("http://", "https://")):
                return {"ok": False, "error": f"server '{name}' 的 url 必须以 http(s):// 开头"}
            entry["url"] = url
        headers = srv.get("headers")
        if isinstance(headers, dict) and headers:
            entry["headers"] = headers
        cleaned.append(entry)

    cleaned = _unmask_mcp_headers(cleaned, current)
    _save_env_updates({"MCP_SERVERS": json.dumps(cleaned, ensure_ascii=False)})
    get_settings.cache_clear() if hasattr(get_settings, "cache_clear") else None

    from crawagent.graph.skills import get_mcp_tools, reset_mcp_cache
    from crawagent.web.state import reset_agent_cache

    reset_mcp_cache()
    reset_agent_cache()
    # 预热握手：让保存完立刻能看到各 server 工具数（失败也不阻塞保存本身）
    try:
        await asyncio.to_thread(get_mcp_tools)
    except Exception:
        pass

    from crawagent.graph.skills import mcp_servers_status
    try:
        raw = json.loads(get_settings().mcp_servers)
    except json.JSONDecodeError:
        raw = []
    return {"ok": True, "mcp_servers": _mask_mcp_headers(raw), "mcp_status": mcp_servers_status()}


@router.post("/api/ecosystem/open-folder")
async def open_ecosystem_folder_route(payload: dict = Body(...)) -> dict[str, Any]:
    """在资源管理器中打开 skills/ / plugins/ / 产物 / 下载目录（本地部署，便于用户放文件与查产物）。"""
    folder = str(payload.get("folder") or "").strip()
    if folder in ("output", "downloads"):
        s = get_settings()
        root = Path(s.output_dir if folder == "output" else s.downloads_dir)
    elif folder in ("skills", "plugins"):
        root = Path(__file__).resolve().parents[3] / folder
    else:
        return {"ok": False, "error": "folder 只支持 skills / plugins / output / downloads"}
    root.mkdir(exist_ok=True)
    import os

    os.startfile(str(root)) if hasattr(os, "startfile") else None
    return {"ok": True, "path": str(root)}


# ---------------------------------------------------------------------------
# 聊天背景图（ADR-0001）：存服务端 data/，任何 origin / 浏览器共享同一份。
# localStorage 按 origin 隔离（localhost:8006 ≠ 127.0.0.1:8006）且 ~5MB 配额易爆，已弃用。
# ---------------------------------------------------------------------------

_DATA_DIR = Path(__file__).resolve().parents[3] / "data"
_BG_IMAGE = _DATA_DIR / "background.jpg"
_BG_META = _DATA_DIR / "background.json"
_BG_PREFIX = "data:image/jpeg;base64,"
_MAX_BG_BYTES = 8 * 1024 * 1024  # 前端统一 canvas 压缩后 ~200-400KB，此为防御上限


def _bg_meta() -> dict:
    try:
        data = json.loads(_BG_META.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _bg_state() -> dict[str, Any]:
    return {
        "image": f"/api/background/image?t={_BG_IMAGE.stat().st_mtime_ns}" if _BG_IMAGE.exists() else None,
        "opacity": _bg_meta().get("opacity", 60),
    }


@router.get("/api/background")
async def get_background_route() -> dict[str, Any]:
    """背景图当前状态（image 为带 mtime 防缓存的相对 URL，无背景时为 null）"""
    return _bg_state()


@router.get("/api/background/image")
async def get_background_image_route() -> FileResponse:
    if not _BG_IMAGE.exists():
        raise HTTPException(status_code=404, detail="no background image")
    return FileResponse(_BG_IMAGE, media_type="image/jpeg", headers={"Cache-Control": "no-cache"})


@router.post("/api/background")
async def save_background_route(payload: dict = Body(...)) -> dict[str, Any]:
    """保存背景图：data_url 为前端 canvas 压缩后的 JPEG dataURL，落盘 data/background.jpg"""
    data_url = str(payload.get("data_url") or "")
    if not data_url.startswith(_BG_PREFIX):
        return {"ok": False, "error": "data_url 必须是 data:image/jpeg;base64,…（前端统一压缩为 JPEG）"}
    try:
        raw = base64.b64decode(data_url[len(_BG_PREFIX):], validate=False)
    except ValueError as e:
        return {"ok": False, "error": f"base64 解码失败: {e}"}
    if not raw:
        return {"ok": False, "error": "图片内容为空"}
    if len(raw) > _MAX_BG_BYTES:
        return {"ok": False, "error": f"图片解压后 {len(raw) // 1024 // 1024}MB 超出上限，请换小图"}
    _DATA_DIR.mkdir(exist_ok=True)
    _BG_IMAGE.write_bytes(raw)
    return {"ok": True, **_bg_state()}


@router.post("/api/background/opacity")
async def save_background_opacity_route(payload: dict = Body(...)) -> dict[str, Any]:
    """保存遮罩浓度（0-95）。前端拖动滑块防抖后调用，独立于背景图存储"""
    try:
        opacity = int(payload.get("opacity"))
    except (TypeError, ValueError):
        return {"ok": False, "error": "opacity 必须是整数"}
    if not 0 <= opacity <= 95:
        return {"ok": False, "error": "opacity 需在 0-95 之间"}
    _DATA_DIR.mkdir(exist_ok=True)
    meta = _bg_meta()
    meta["opacity"] = opacity
    _BG_META.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    return {"ok": True, "opacity": opacity}


@router.delete("/api/background")
async def clear_background_route() -> dict[str, Any]:
    """清除背景图与遮罩浓度设置"""
    _BG_IMAGE.unlink(missing_ok=True)
    _BG_META.unlink(missing_ok=True)
    return {"ok": True}
    """打开启动脚本文件夹，让用户双击 bat 启动 anything-analyzer。"""
    from crawagent.graph.skills import _port_listening, _find_anything_analyzer, _find_pnpm_cmd, _build_autostart_command
    from pathlib import Path
    import asyncio, os, re

    # 先同步 Electron token
    from crawagent.tools.mcp_capture_tool import auto_sync_mcp_token
    auto_sync_mcp_token()

    s = get_settings()
    try:
        first = json.loads(s.mcp_servers)[0] if s.mcp_servers.strip() else {}
        from urllib.parse import urlparse
        port = urlparse(first.get("url", "")).port or 23816
    except Exception:
        port = 23816

    if port and _port_listening("127.0.0.1", port):
        return {"ok": True, "message": f"MCP 服务已在运行（127.0.0.1:{port}）", "already_running": True}

    aa_path = _find_anything_analyzer()
    if not aa_path:
        return {"ok": False, "error": "没找到 anything-analyzer 项目，请在 .env 里加 ANYTHING_ANALYZER_PATH"}

    # 1. 生成 bat 文件
    scripts_dir = Path(__file__).parent.parent / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    bat_path = scripts_dir / "start_anything_analyzer.bat"

    # 用干净的方式构建 bat —— 直接调 _find_pnpm_cmd
    from crawagent.graph.skills import _find_pnpm_cmd
    pnpm = _find_pnpm_cmd()
    exe_path = pnpm
    exe_args = "dev"

    bat_path.write_text(
        "@echo off\r\n"
        "chcp 65001 >nul\r\n"
        "title anything-analyzer MCP Server\r\n"
        "echo.\r\n"
        "echo === anything-analyzer ===\r\n"
        f'cd /d "{aa_path}"\r\n'
        f'call "{exe_path}" {exe_args}\r\n'
        "echo.\r\n"
        "pause >nul\r\n",
        encoding="utf-8"
    )

    # 2. 直接执行 bat（和手动双击完全等价，shell32 绕开 Trae sandbox）
    if os.name == "nt":
        os.startfile(str(bat_path))

    return {
        "ok": True,
        "message": "已启动 anything-analyzer —— 会弹一个 cmd 窗口自动跑 pnpm dev",
        "bat_path": str(bat_path),
        "aa_path": aa_path,
        "already_running": False,
    }
