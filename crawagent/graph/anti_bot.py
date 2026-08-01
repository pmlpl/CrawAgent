from __future__ import annotations

import asyncio
import json
from enum import Enum
from typing import Any, Dict, List, Optional, TypedDict

from pydantic import BaseModel, Field

from crawagent.llm.factory import get_llm


class AntiBotChallenge(str, Enum):
    CLOUDFLARE = "cloudflare"
    SLIDER_CAPTCHA = "slider_captcha"
    RECAPTCHA = "recaptcha"
    WAF = "waf"
    RATE_LIMIT = "rate_limit"
    UNKNOWN = "unknown"


class StrategyUpgrade(BaseModel):
    action: str  # "switch_proxy", "use_curl_cffi", "use_browser", "wait_and_retry", "solve_captcha"
    params: Dict = {}
    reason: str = ""


class AntiBotState(TypedDict):
    challenge_type: str
    current_strategy: str
    retry_count: int
    max_retries: int
    response_headers: Dict
    response_status: int
    error_message: str
    upgrade: Optional[StrategyUpgrade]


class AntiBotAction:
    """反爬升级动作"""

    @staticmethod
    def switch_proxy() -> StrategyUpgrade:
        return StrategyUpgrade(
            action="switch_proxy",
            reason="IP 可能被封禁，切换代理"
        )

    @staticmethod
    def use_curl_cffi(impersonate: str = "chrome120") -> StrategyUpgrade:
        return StrategyUpgrade(
            action="use_curl_cffi",
            params={"impersonate": impersonate},
            reason="切换到 curl_cffi impersonate 模式绕过 TLS 指纹检测"
        )

    @staticmethod
    def use_browser(stealth: bool = True) -> StrategyUpgrade:
        return StrategyUpgrade(
            action="use_browser",
            params={"stealth": stealth},
            reason="启用无头浏览器渲染 JS"
        )

    @staticmethod
    def wait_and_retry(delay: float = 5.0) -> StrategyUpgrade:
        return StrategyUpgrade(
            action="wait_and_retry",
            params={"delay": delay},
            reason="触发限流，等待后重试"
        )

    @staticmethod
    def solve_captcha(service: str = "2captcha") -> StrategyUpgrade:
        return StrategyUpgrade(
            action="solve_captcha",
            params={"service": service},
            reason="遇到验证码，调用打码平台"
        )

    @staticmethod
    def human_intervention() -> StrategyUpgrade:
        return StrategyUpgrade(
            action="human_intervention",
            reason="无法自动处理，需要人工介入"
        )


ANTI_BOT_PROMPT = """你是反爬对抗专家。根据当前情况，决定下一步升级策略。

当前情况：
- 挑战类型：{challenge_type}
- 当前策略：{current_strategy}
- 重试次数：{retry_count}/{max_retries}
- 响应状态：{response_status}
- 错误信息：{error_message}
- 响应头关键字段：{response_headers}

可选策略（按激进程度排序）：
1. wait_and_retry - 等待后重试（适用于限流）
2. switch_proxy - 切换代理 IP（IP 被封）
3. use_curl_cffi - 使用 curl_cffi impersonate 浏览器 TLS 指纹（Cloudflare、WAF 指纹检测）
4. use_browser - 启用无头浏览器渲染 JS（JS 挑战、滑块验证码）
5. solve_captcha - 调用打码平台（reCAPTCHA、滑块）
6. human_intervention - 无法自动处理，需人工介入

原则：
- 优先尝试低成本策略
- 同一策略最多重试 2 次
- Cloudflare 5 秒盾 -> use_curl_cffi -> use_browser
- 限流 429 -> wait_and_retry -> switch_proxy
- 验证码 -> solve_captcha -> human_intervention
- 连续 3 次同策略失败 -> 升级下一级

输出 JSON：
{
    "action": "策略名",
    "params": {},
    "reason": "选择理由"
}"""


async def detect_challenge(response_headers: Dict, response_status: int, error: str) -> str:
    """检测反爬类型"""
    headers_str = str(response_headers).lower()
    
    if response_status == 429:
        return "rate_limit"
    
    if "cloudflare" in headers_str or "cf-ray" in headers_str:
        return "cloudflare"
    
    if "recaptcha" in error.lower() or "g-recaptcha" in error.lower():
        return "recaptcha"
    
    if "slider" in error.lower() or "滑块" in error:
        return "slider_captcha"
    
    if any(waf in headers_str for waf in ["waf", "akamai", "incapsula", "sucuri", "imperva"]):
        return "waf"
    
    return "unknown"


async def decide_anti_bot_upgrade(state: AntiBotState) -> AntiBotState:
    """LLM 决定反爬升级策略"""
    llm = get_llm()
    
    prompt = ANTI_BOT_PROMPT.format(
        challenge_type=state["challenge_type"],
        current_strategy=state["current_strategy"],
        retry_count=state["retry_count"],
        max_retries=state["max_retries"],
        response_status=state["response_status"],
        error_message=state["error_message"],
        response_headers=json.dumps({k: v for k, v in state["response_headers"].items() 
                                     if k.lower() in ("server", "cf-ray", "x-cache", "x-powered-by", "set-cookie")},
                                     ensure_ascii=False),
    )
    
    try:
        llm_resp = await get_llm().ainvoke([
            {"role": "system", "content": "你是反爬对抗专家，输出严格 JSON。"},
            {"role": "user", "content": prompt}
        ])
        import re
        json_match = re.search(r'\{.*\}', llm_resp.content, re.DOTALL)
        if json_match:
            upgrade_data = json.loads(json_match.group())
        else:
            upgrade_data = json.loads(llm_resp.content)
        
        state["upgrade"] = StrategyUpgrade(**upgrade_data)
    except Exception:
        # 兜底：按规则升级
        state["upgrade"] = fallback_upgrade(state)
    
    return state


def fallback_upgrade(state: AntiBotState) -> StrategyUpgrade:
    """规则兜底升级"""
    challenge = state["challenge_type"]
    strategy = state["current_strategy"]
    retry = state["retry_count"]
    
    # 同策略超过 2 次，升级
    if retry >= 2:
        if strategy == "httpx":
            return AntiBotAction.use_curl_cffi()
        elif strategy == "curl_cffi":
            return AntiBotAction.use_browser()
        elif strategy == "browser":
            if state["challenge_type"] in ("recaptcha", "slider_captcha"):
                return AntiBotAction.solve_captcha()
            return AntiBotAction.human_intervention()
    
    # 首次根据类型选择
    if challenge == "rate_limit":
        return AntiBotAction.wait_and_retry(delay=5.0)
    elif challenge == "cloudflare":
        if strategy == "httpx":
            return AntiBotAction.use_curl_cffi()
        return AntiBotAction.use_browser()
    elif challenge in ("recaptcha", "slider_captcha"):
        return AntiBotAction.solve_captcha()
    elif challenge == "waf":
        if strategy == "httpx":
            return AntiBotAction.use_curl_cffi()
        return AntiBotAction.use_browser()
    else:
        return AntiBotAction.switch_proxy()


async def handle_anti_bot_challenge_simple(
    challenge_type: str,
    current_strategy: str,
    retry_count: int,
    max_retries: int,
    response_headers: Dict,
    response_status: int,
    error_message: str,
) -> StrategyUpgrade:
    """简化版外部调用入口：直接检测+决策，返回 StrategyUpgrade"""
    state = AntiBotState(
        challenge_type=challenge_type,
        current_strategy=current_strategy,
        retry_count=retry_count,
        max_retries=max_retries,
        response_headers=response_headers,
        response_status=response_status,
        error_message=error_message,
        upgrade=None,
    )

    # 重新检测挑战类型（覆盖传入值）
    detected_type = await detect_challenge(
        state["response_headers"], state["response_status"], state["error_message"]
    )
    state["challenge_type"] = detected_type

    state = await decide_anti_bot_upgrade(state)

    return state["upgrade"] or AntiBotAction.human_intervention()


# ==================== LangGraph 子图 ====================

def build_anti_bot_graph():
    """构建反爬处理子图"""
    from langgraph.graph import StateGraph, END
    
    graph = StateGraph(AntiBotState)
    
    graph.add_node("detect", detect_node)
    graph.add_node("decide", decide_node)
    graph.add_node("apply", apply_upgrade_node)
    
    graph.set_entry_point("detect")
    graph.add_edge("detect", "decide")
    graph.add_edge("decide", "apply")
    graph.add_edge("apply", END)
    
    return graph.compile()


async def detect_node(state: AntiBotState) -> AntiBotState:
    state["challenge_type"] = await detect_challenge(
        state["response_headers"],
        state["response_status"],
        state["error_message"]
    )
    return state


async def decide_node(state: AntiBotState) -> AntiBotState:
    return await decide_anti_bot_upgrade(state)


async def apply_upgrade_node(state: AntiBotState) -> AntiBotState:
    """应用升级（实际执行由外部调用器完成，这里只记录）"""
    upgrade = state.get("upgrade")
    if upgrade:
        print(f"[AntiBot] 升级策略: {upgrade.action} - {upgrade.reason}")
    return state


async def handle_anti_bot_challenge(
    challenge_type: str,
    current_strategy: str,
    retry_count: int,
    max_retries: int,
    response_headers: Dict,
    response_status: int,
    error_message: str,
) -> Dict:
    """外部调用入口：通过子图节点处理反爬挑战，返回 Dict"""
    state = AntiBotState(
        challenge_type=challenge_type,
        current_strategy=current_strategy,
        retry_count=retry_count,
        max_retries=max_retries,
        response_headers=response_headers,
        response_status=response_status,
        error_message=error_message,
        upgrade=None,
    )

    state = await detect_node(state)
    state = await decide_node(state)
    state = await apply_upgrade_node(state)

    upgrade = state.get("upgrade")
    if upgrade:
        return upgrade.model_dump()
    return {"action": "human_intervention", "reason": "无法确定策略"}


# ==================== P1-3: is_blocked() 三层检测 ====================

class BlockResult(BaseModel):
    """反爬检测结果"""
    blocked: bool = False
    challenge_type: str = "none"  # none / cloudflare / waf / rate_limit / captcha / forbidden / js_challenge
    confidence: float = 0.0
    evidence: str = ""
    recommended_action: str = ""  # use_curl_cffi / use_browser / wait_and_retry / solve_captcha / switch_proxy


# 三层检测的信号特征
_BLOCK_STATUS_CODES = {401, 403, 429, 503}

_CLOUDFLARE_HEADER_SIGNALS = {"cf-ray", "cf-mitigated", "server: cloudflare"}
_CLOUDFLARE_BODY_SIGNALS = ["challenge-platform", "cf-browser-verification", "cf_chl_opt", "just a moment"]

_WAF_HEADER_SIGNALS = {"akamai", "incapsula", "sucuri", "imperva", "x-cdn: incapsula"}
_WAF_BODY_SIGNALS = ["access denied", "request blocked", "security check", "incapsula incident", "sucuri firewall"]

_CAPTCHA_BODY_SIGNALS = ["g-recaptcha", "h-captcha", "captcha-container", "slider", "gt_slider", "geetest"]

_JS_CHALLENGE_SIGNALS = ["enable javascript", "please enable javascript", "noscript", "javascript is disabled"]


def is_blocked(
    status_code: int = 0,
    headers: Optional[Dict] = None,
    body: str = "",
    error: str = "",
) -> BlockResult:
    """三层反爬检测：状态码 + 响应头 + 响应体

    Layer 1 — 状态码：401/403/429/503 直接判定
    Layer 2 — 响应头：Cloudflare/WAF 指纹
    Layer 3 — 响应体：CAPTCHA / JS 挑战 / WAF 拦截页

    Args:
        status_code: HTTP 状态码
        headers: 响应头字典
        body: 响应体文本（前几 KB 即可）
        error: 错误信息

    Returns:
        BlockResult: 是否被封、挑战类型、建议动作
    """
    headers = headers or {}
    headers_lower = {k.lower(): str(v).lower() for k, v in headers.items()}
    headers_str = str(headers_lower)
    body_lower = body.lower()[:8192] if body else ""
    error_lower = (error or "").lower()

    # ---- Layer 1: 状态码 ----
    if status_code == 429:
        return BlockResult(
            blocked=True,
            challenge_type="rate_limit",
            confidence=0.95,
            evidence=f"HTTP {status_code}",
            recommended_action="wait_and_retry",
        )

    if status_code in (401, 403):
        return BlockResult(
            blocked=True,
            challenge_type="forbidden",
            confidence=0.85,
            evidence=f"HTTP {status_code}",
            recommended_action="use_curl_cffi",
        )

    if status_code == 503:
        # 503 可能是 Cloudflare 或临时维护
        if any(sig in headers_str for sig in _CLOUDFLARE_HEADER_SIGNALS):
            return BlockResult(
                blocked=True,
                challenge_type="cloudflare",
                confidence=0.9,
                evidence=f"HTTP 503 + Cloudflare headers",
                recommended_action="use_browser",
            )
        return BlockResult(
            blocked=True,
            challenge_type="service_unavailable",
            confidence=0.6,
            evidence=f"HTTP 503",
            recommended_action="wait_and_retry",
        )

    # ---- Layer 2: 响应头指纹 ----
    for sig in _CLOUDFLARE_HEADER_SIGNALS:
        if sig in headers_str:
            return BlockResult(
                blocked=True,
                challenge_type="cloudflare",
                confidence=0.8,
                evidence=f"Header signal: {sig}",
                recommended_action="use_browser",
            )

    for sig in _WAF_HEADER_SIGNALS:
        if sig in headers_str:
            return BlockResult(
                blocked=True,
                challenge_type="waf",
                confidence=0.75,
                evidence=f"WAF header: {sig}",
                recommended_action="use_curl_cffi",
            )

    # ---- Layer 3: 响应体内容 ----
    for sig in _CLOUDFLARE_BODY_SIGNALS:
        if sig in body_lower:
            return BlockResult(
                blocked=True,
                challenge_type="cloudflare",
                confidence=0.85,
                evidence=f"Body signal: {sig}",
                recommended_action="use_browser",
            )

    for sig in _WAF_BODY_SIGNALS:
        if sig in body_lower:
            return BlockResult(
                blocked=True,
                challenge_type="waf",
                confidence=0.7,
                evidence=f"Body signal: {sig}",
                recommended_action="use_curl_cffi",
            )

    for sig in _CAPTCHA_BODY_SIGNALS:
        if sig in body_lower:
            return BlockResult(
                blocked=True,
                challenge_type="captcha",
                confidence=0.8,
                evidence=f"Captcha signal: {sig}",
                recommended_action="solve_captcha",
            )

    for sig in _JS_CHALLENGE_SIGNALS:
        if sig in body_lower:
            return BlockResult(
                blocked=True,
                challenge_type="js_challenge",
                confidence=0.65,
                evidence=f"JS challenge: {sig}",
                recommended_action="use_browser",
            )

    # ---- 错误信息兜底 ----
    if "timeout" in error_lower or "connection refused" in error_lower:
        return BlockResult(
            blocked=False,
            challenge_type="network_error",
            confidence=0.5,
            evidence=error,
            recommended_action="wait_and_retry",
        )

    return BlockResult(blocked=False, challenge_type="none", confidence=0.0)


def is_blocked_from_result(result: Any) -> BlockResult:
    """从 CrawlResult 对象快速检测

    Args:
        result: CrawlResult 或包含 status_code/headers/html/error 的 dict

    Returns:
        BlockResult
    """
    if hasattr(result, "status_code"):
        # CrawlResult 对象
        return is_blocked(
            status_code=getattr(result, "status_code", 0) or 0,
            headers=getattr(result, "headers", None),
            body=getattr(result, "html", "") or "",
            error=getattr(result, "error", "") or "",
        )
    elif isinstance(result, dict):
        return is_blocked(
            status_code=result.get("status_code", 0) or result.get("status", 0),
            headers=result.get("headers"),
            body=result.get("html", "") or result.get("content", "") or "",
            error=result.get("error", "") or "",
        )
    return BlockResult(blocked=False)