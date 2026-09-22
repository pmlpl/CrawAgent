"""设置相关 API：多服务商模型注册表的增删改查、思考深度与连通性测试。

数据存储在 .env 的 LLM_PROVIDERS 键（JSON 数组），结构见 crawagent/llm/registry.py。
Key 按服务商共享：同一服务商下的多个模型共用一个 api_key。
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import FileResponse

from crawagent.config.settings import get_settings
from crawagent.llm.registry import load_providers
from crawagent.web.state import reset_agent_cache

router = APIRouter()

ENV_FILE = Path(__file__).resolve().parents[4] / ".env"


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
        # ---- LangSmith 追踪（可选调试）：key 脱敏回显，含 * 视为掩码不回写 ----
        "langsmith_api_key": _mask_key(s.langsmith_api_key),
        "langsmith_project": s.langsmith_project,
        "langsmith_tracing": s.langsmith_tracing,
    }


def _mask_key(raw: str) -> str:
    """API Key 脱敏回显：空返 ""，否则 ****+末4位。回写时含 * 视为掩码、还原原值。"""
    if not raw:
        return ""
    return "****" + raw[-4:] if len(raw) > 4 else "****"


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
            p = Path(__file__).resolve().parents[4] / p
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

    # ---- LangSmith 追踪：key 含 * 视为掩码跳过；project/tracing 直写 ----
    if "langsmith_api_key" in payload:
        k = str(payload.get("langsmith_api_key") or "")
        if k and "*" not in k:  # 掩码不回写，沿用 .env 原值
            updates["LANGSMITH_API_KEY"] = k
    if "langsmith_project" in payload:
        updates["LANGSMITH_PROJECT"] = str(payload.get("langsmith_project") or "crawagent")
    if "langsmith_tracing" in payload:
        updates["LANGSMITH_TRACING"] = "true" if payload.get("langsmith_tracing") else "false"

    if updates:
        _save_env_updates(updates)
        # get_settings 若有缓存则清（让 Settings 重读 .env）；tracing 由 langchain 运行时启动读 env，仍需重启 crawagent 生效
        get_settings.cache_clear() if hasattr(get_settings, "cache_clear") else None

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


