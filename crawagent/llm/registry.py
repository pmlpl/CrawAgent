"""多服务商模型注册表 — 从 .env 文件直接读取（绕过 pydantic-settings 缓存）。

数据结构（.env 的 LLM_PROVIDERS 键，JSON 数组）：
    [{"name": "DeepSeek", "base_url": "https://api.deepseek.com/v1",
      "api_key": "sk-...", "models": ["deepseek-chat", ...]}, ...]

Key 按服务商共享：同一服务商下的所有模型共用一个 api_key。
注册表为空时回退到旧全局配置（openai_api_key / openai_base_url / default_model）。

⚠️ 为什么不调 get_settings().llm_providers？
    pydantic-settings 2.x 的 DotEnvSettingsSource 内部用 @cached_property
    缓存 env_vars，Settings() 重新构造不会重新读 .env —— 表现为"删了模型但还在"。
    直接读磁盘彻底绕过这个缓存层。
"""
from __future__ import annotations

import json
from pathlib import Path

from crawagent.config.settings import get_settings


def _read_env_file() -> Path | None:
    """拿到 .env 路径：优先用 get_settings() 里的 model_config['env_file']"""
    try:
        env_file = get_settings().model_config.get('env_file')
        if env_file:
            if isinstance(env_file, (list, tuple)):
                env_file = env_file[0] if env_file else None
            if env_file:
                return Path(env_file)
    except Exception:
        pass
    # 回退：项目根目录的 .env
    from crawagent.config.settings import Settings
    try:
        return Path(__file__).resolve().parents[2] / '.env'
    except Exception:
        return None


def load_providers() -> list[dict]:
    """直接读磁盘 .env 里的 LLM_PROVIDERS — 绕过 pydantic-settings 缓存层。"""
    env_path = _read_env_file()
    if env_path is None or not env_path.is_file():
        return []
    try:
        for line in env_path.read_text(encoding='utf-8').splitlines():
            stripped = line.strip()
            if stripped.startswith('LLM_PROVIDERS='):
                raw = stripped.split('=', 1)[1].strip()
                if not raw:
                    return []
                data = json.loads(raw)
                return data if isinstance(data, list) else []
        return []
    except (json.JSONDecodeError, UnicodeDecodeError):
        return []


def resolve_model(model: str | None = None) -> tuple[str, str, str, str]:
    """把模型 ID 解析为 (model, base_url, api_key, adapter)。

    adapter 取值：
      - "deepseek" — DeepSeek 原生，支持 thinking.type + reasoning_effort（deepseek 值域）
      - "zhipu"    — 智谱 GLM 系列，thinking.type + reasoning_effort（zhipu 值域）
      - "openai"   — 通用 OpenAI 兼容中转站，不传思考模式参数
      - 其他/缺失  — 按 model 名称自动判断（glm→zhipu, deepseek→deepseek, 其余→openai）

    - model 在注册表中：返回其服务商的 base_url / api_key / adapter
    - model 为 None 或未收录：回退到注册表中第一个可用模型
    - 注册表为空：回退到旧全局配置（openai_base_url / openai_api_key / default_model）
    """
    for p in load_providers():
        models = p.get("models") or []
        if model:
            if model in models:
                return model, p.get("base_url", ""), p.get("api_key", ""), _infer_adapter(p, model)
        elif models:
            m = models[0]
            return m, p.get("base_url", ""), p.get("api_key", ""), _infer_adapter(p, m)

    # 回退：旧全局配置
    s = get_settings()
    m = model or s.default_model
    return m, s.openai_base_url, s.openai_api_key, _infer_adapter({}, m)


def _infer_adapter(provider: dict, model: str) -> str:
    """从 provider 字典里的 'adapter' 字段取适配器类型；缺失则按模型名推断。"""
    explicit = (provider.get("adapter") or "").strip().lower()
    if explicit in ("deepseek", "zhipu", "openai"):
        return explicit
    # 按模型名推断
    m = (model or "").lower()
    if "glm" in m or "zhipu" in m:
        return "zhipu"
    if "deepseek" in m:
        return "deepseek"
    return "openai"


def provider_context_window(model: str | None = None) -> int | None:
    """按模型名查所属 provider 配置里手填的 context_window（token）。

    优先级：provider 显式填了 context_window 且 models 收录了该模型 → 返回该值；
    否则返回 None（调用方回退到内置静态表 _CONTEXT_WINDOW_TABLE）。

    用途：中转站/代理商的模型名五花八门、内置前缀表覆盖不到时，用户可在
    LLM_PROVIDERS 的 provider 对象里直接写 "context_window": 1048576（最高 1M）覆盖，
    比维护静态表更可靠、更直接。
    """
    if not model:
        return None
    target = str(model).lower()
    for p in load_providers():
        cw = p.get("context_window")
        if not cw:
            continue
        models = [str(m).lower() for m in (p.get("models") or [])]
        if target in models:
            return int(cw)
    return None
