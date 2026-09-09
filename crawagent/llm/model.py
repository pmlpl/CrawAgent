"""模型对接层 — 多 Provider 统一适配（DeepSeek / 智谱 GLM / 通用 OpenAI 兼容）。

## Provider 适配架构

三种 adapter 按 `resolve_model()` 返回值分支：

| adapter   | thinking 参数 | reasoning_effort 值域 | 说明 |
|-----------|---------------|----------------------|------|
| deepseek  | ✅ enabled/disabled | none/low/medium/high/xhigh | DeepSeek 原生 |
| zhipu     | ✅ enabled/disabled | none/minimal/low/medium/high/xhigh/max | GLM-5.2 支持全值域，GLM-5.3 只接受 low/high/max 且强制 enabled |
| openai    | ❌ 不传 | ❌ 不传 | 通用中转站，不传思考参数避免报错 |

注册表（.env 的 LLM_PROVIDERS）里每个 provider 可显式加 `"adapter": "zhipu"`；
缺失时按模型名自动推断（glm→zhipu, deepseek→deepseek, 其余→openai）。

## Context Window 查表

不同模型的最大上下文差别巨大（32K → 1M）。`get_context_window(model)` 返回
模型对应的 context window 上限（token），middleware 据此动态调整裁剪水位。
"""
from langchain_openai import ChatOpenAI

from crawagent.config.settings import get_settings
from crawagent.llm.registry import resolve_model


# ── Context Window 查表（单位：token，prompt+output 总和上限） ──────
# 按模型名前缀匹配，第一个命中生效。
# 中转站走最后一条 DEFAULT_FALLBACK。
_CONTEXT_WINDOW_TABLE: list[tuple[str, int]] = [
    # —— DeepSeek 系列 ——
    ("deepseek-reasoner", 64_000),
    ("deepseek-chat", 64_000),
    ("deepseek-v3", 128_000),
    ("deepseek", 64_000),  # 兜底

    # —— 智谱 GLM 系列 ——
    ("glm-5.3", 1_000_000),      # GLM-5.3 官方 1M
    ("glm-5-130b", 1_000_000),   # GLM-5 130B 1M
    ("glm-5", 1_000_000),        # GLM-5.x 全系列 1M
    ("glm-4", 128_000),          # GLM-4 / 4V 系列 128K
    ("glm", 128_000),            # 兜底

    # —— OpenAI 系列 ——
    ("gpt-4o-128k", 128_000),
    ("gpt-4o", 128_000),
    ("gpt-4-128k", 128_000),
    ("gpt-4", 8_192),
    ("gpt-3.5-turbo-128k", 128_000),
    ("gpt-3.5", 16_384),

    # —— 其他常见 ——
    ("qwen-max-1201", 1_000_000),  # Qwen-Max 1M
    ("qwen-plus", 128_000),
    ("qwen", 32_768),
    ("claude-3-opus", 200_000),
    ("claude-3.5-sonnet", 200_000),
    ("claude", 200_000),
    ("gemini-1.5-pro", 1_000_000),
    ("gemini", 32_768),
    ("moonshot-v1-128k", 128_000),
    ("moonshot", 32_768),
    ("kimi", 128_000),
    ("minimax", 100_000),

    # —— 中转站兜底 ——
    ("", 32_000),  # 最低兜底 32K（旧 DeepSeek-V3 级别）
]


def get_context_window(model: str | None = None) -> int:
    """按模型名查 context window 上限（token）。

    优先级：
    1. provider 配置显式手填的 context_window（LLM_PROVIDERS 里加
       "context_window": 1048576，最高 1M）——中转站模型名静态表覆盖不到时最可靠；
    2. 内置静态前缀表 _CONTEXT_WINDOW_TABLE；
    3. 最低兜底 32K。

    返回的是 prompt+output 总和上限；middleware 裁剪水位设为窗口的 70%，
    给 LLM 输出留够余量。
    """
    name = (model or "").lower().strip()
    if name:
        # ① provider 配置手填覆盖（最高优先级）
        from crawagent.llm.registry import provider_context_window
        override = provider_context_window(model)
        if override:
            return override
    if not name:
        return _CONTEXT_WINDOW_TABLE[-1][1]

    # ② 静态前缀表（长的先试，避免 "glm" 先匹配掉 "glm-5.3"）
    for prefix, window in _CONTEXT_WINDOW_TABLE:
        if prefix and name.startswith(prefix):
            return window

    return _CONTEXT_WINDOW_TABLE[-1][1]


# ── DeepSeek reasoning_effort 值域 ──────────────────────────────────
_DEEPSEEK_EFFORT_MAP = {
    "low": "low",
    "medium": "medium",
    "high": "high",
    "max": "xhigh",
}

# ── 智谱 GLM reasoning_effort 值域 ────────────────────────────────
# GLM-5.2: none/minimal/low/medium/high/xhigh/max 全支持
# GLM-5.3: 只接受 low/high/max，其他报错
_ZHIPU_EFFORT_MAP = {
    "low": "low",
    "medium": "high",
    "high": "max",
    "max": "max",
}


def _ensure_no_proxy_for(base_url: str) -> None:
    """若 LLM base_url 是 loopback/私网地址（含 CGNAT 100.64/10，Tailscale 走这个），
    把 host 加进 NO_PROXY，避免 openai SDK 的 httpx 把本地/内网 LLM 请求塞进
    系统代理（Privoxy 之类）转发失败（500 no-server-data）。公网域名不动——
    那些可能本就需要代理才能到（用户的网络到 api.deepseek.com 直连 000）。
    """
    import ipaddress
    import os
    from urllib.parse import urlparse

    try:
        host = (urlparse(base_url).hostname or "").lower()
    except Exception:
        return
    if not host:
        return
    bypass = False
    if host in ("localhost", "::1") or host.startswith("127."):
        bypass = True
    else:
        try:
            ip = ipaddress.ip_address(host)
            # Python 3.13 起 is_private 不再含 CGNAT 100.64/10（Tailscale 走这段），显式补上
            bypass = bool(
                ip.is_private
                or ip.is_loopback
                or ip.is_link_local
                or (ip.version == 4 and ip in ipaddress.ip_network("100.64.0.0/10"))
            )
        except ValueError:
            bypass = False  # 公网域名，不自动 bypass
    if not bypass:
        return
    cur = os.environ.get("NO_PROXY", "")
    parts = [p.strip() for p in cur.split(",") if p.strip()]
    if host not in parts:
        parts.append(host)
        os.environ["NO_PROXY"] = ",".join(parts)


def get_llm(
    model: str | None = None,
    thinking: bool | None = None,
    max_tokens: int | None = None,
) -> ChatOpenAI:
    """获取 LLM 实例（兼容 OpenAI 接口格式，模型/Key/Adapter 由注册表解析）

    thinking: None=按 settings.thinking_depth；False=强制关闭（缓存预热用，
    避免 thinking 模式下 max_tokens=1 引发兼容问题）。
    max_tokens: None=8192；缓存预热传 1 把输出成本压到最低。

    Returns:
        ChatOpenAI 实例，支持 tool calling + 自动重试（3 次）
    """
    model_name, base_url, api_key, adapter = resolve_model(model)
    _ensure_no_proxy_for(base_url)

    # ── 基础 kwargs（所有 provider 共用） ──────────────────────────
    kwargs: dict = {
        "api_key": api_key,
        "base_url": base_url,
        "model": model_name,
        "timeout": 120,
        # 重试策略：3 次重试（含首次共 4 次调用机会）
        # langchain 内部用 tenacity，自动处理 429/5xx/连接超时
        "max_retries": 3,
        "max_tokens": max_tokens or 8192,
    }

    depth = (get_settings().thinking_depth or "").strip().lower()
    if thinking is False:
        depth = "off"  # 缓存预热强制关闭

    # ── 按 adapter 分支处理思考模式参数 ────────────────────────────
    if adapter == "openai":
        # 通用中转站：不传任何思考参数，避免字段不识别报错
        kwargs["temperature"] = 0
    elif adapter == "zhipu":
        _apply_zhipu_params(kwargs, depth)
    else:  # deepseek
        _apply_deepseek_params(kwargs, depth)

    return ChatOpenAI(**kwargs)


def _apply_deepseek_params(kwargs: dict, depth: str) -> None:
    """DeepSeek 思考模式：thinking.type + reasoning_effort（deepseek 值域）"""
    if not depth or depth == "off":
        # 关闭思考模式
        kwargs["temperature"] = 0
        kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
    else:
        kwargs["extra_body"] = {"thinking": {"type": "enabled"}}
        kwargs["reasoning_effort"] = _DEEPSEEK_EFFORT_MAP.get(depth, "high")
        # 思考模式下 temperature/top_p 不生效，不显式传


def _apply_zhipu_params(kwargs: dict, depth: str) -> None:
    """智谱 GLM 思考模式：thinking.type + reasoning_effort（zhipu 值域）

    注意：GLM-5.3 强制思考不可关闭（传 disabled 会报错）。
    这里保守处理：off 也传 enabled，但 reasoning_effort=none（让模型尽量少思考）。
    """
    if not depth or depth == "off":
        # 关闭思考 —— 智谱 GLM-5.2 支持 disabled，GLM-5.3 不支持
        # 保险起见：仍然传 enabled + reasoning_effort=none（模型会放弃思考）
        kwargs["temperature"] = 0.5
        kwargs["extra_body"] = {"thinking": {"type": "enabled"}}
        kwargs["reasoning_effort"] = "none"
    else:
        kwargs["extra_body"] = {"thinking": {"type": "enabled"}}
        kwargs["reasoning_effort"] = _ZHIPU_EFFORT_MAP.get(depth, "max")
