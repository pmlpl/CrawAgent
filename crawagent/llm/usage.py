"""Token 用量跟踪（P2-6）

参考 Crawl4AI extraction_strategy.py 的 TokenUsage 设计：
- TokenUsageTracker：按模型累计 prompt/completion/total token
- wrap_llm：包装任意 LLM，自动从响应提取用量
- UsageCallbackHandler：LangChain callback 方式采集
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from crawagent.harness.types import TokenUsage


def _extract_usage_from_response(response: Any) -> Dict[str, int]:
    """从 LangChain AIMessage 响应中提取 token 用量（兼容多种格式）。"""
    usage_metadata = getattr(response, "usage_metadata", None)
    if isinstance(usage_metadata, dict) and usage_metadata:
        return {
            "prompt_tokens": usage_metadata.get("input_tokens", 0),
            "completion_tokens": usage_metadata.get("output_tokens", 0),
            "total_tokens": usage_metadata.get("total_tokens", 0),
        }

    response_metadata = getattr(response, "response_metadata", None) or {}
    token_usage = response_metadata.get("token_usage") or response_metadata.get("usage") or {}
    if isinstance(token_usage, dict):
        return {
            "prompt_tokens": token_usage.get("prompt_tokens", 0),
            "completion_tokens": token_usage.get("completion_tokens", 0),
            "total_tokens": token_usage.get("total_tokens", 0),
        }
    return {}


def _extract_model_name(response: Any, fallback: str = "") -> str:
    rm = getattr(response, "response_metadata", None) or {}
    return str(rm.get("model_name") or rm.get("model") or fallback or "unknown")


class TokenUsageTracker:
    """按模型累计 token 用量。"""

    def __init__(self) -> None:
        self._usage: Dict[str, TokenUsage] = {}

    def record(
        self,
        model: str = "",
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
    ) -> None:
        model = model or "unknown"
        current = self._usage.setdefault(
            model, TokenUsage(model=model)
        )
        current.prompt_tokens += int(prompt_tokens or 0)
        current.completion_tokens += int(completion_tokens or 0)
        current.total_tokens += int(prompt_tokens or 0) + int(completion_tokens or 0)

    def record_response(self, response: Any, model: str = "") -> None:
        """从 LLM 响应提取并记录用量。"""
        usage = _extract_usage_from_response(response)
        if not usage.get("total_tokens") and not usage.get("prompt_tokens"):
            return
        self.record(
            model=_extract_model_name(response, model),
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
        )

    def wrap(self, llm: Any, model: str = "") -> Any:
        """包装 LLM：invoke/ainvoke 后自动记录用量。"""
        tracker = self

        class _TrackedLLM:
            def __init__(self, base: Any):
                self._base = base

            async def ainvoke(self, *args, **kwargs):
                response = await self._base.ainvoke(*args, **kwargs)
                tracker.record_response(response, model)
                return response

            def invoke(self, *args, **kwargs):
                response = self._base.invoke(*args, **kwargs)
                tracker.record_response(response, model)
                return response

            def bind_tools(self, *args, **kwargs):
                return _TrackedLLM(self._base.bind_tools(*args, **kwargs))

            def __getattr__(self, name):
                return getattr(self._base, name)

        return _TrackedLLM(llm)

    def snapshot(self) -> Dict[str, Dict[str, int]]:
        """按模型输出用量快照。"""
        return {
            model: {
                "prompt_tokens": u.prompt_tokens,
                "completion_tokens": u.completion_tokens,
                "total_tokens": u.total_tokens,
            }
            for model, u in sorted(self._usage.items())
        }

    def totals(self) -> TokenUsage:
        """全模型合计。"""
        total = TokenUsage()
        for u in self._usage.values():
            total.prompt_tokens += u.prompt_tokens
            total.completion_tokens += u.completion_tokens
            total.total_tokens += u.total_tokens
        return total

    def reset(self) -> None:
        self._usage.clear()


class UsageCallbackHandler:
    """LangChain callback handler：on_llm_end 采集 token 用量。

    用法：llm.with_config({"callbacks": [UsageCallbackHandler(tracker)]})
    """

    def __init__(self, tracker: TokenUsageTracker):
        self.tracker = tracker

    # LangChain 需要的回调名（BaseCallbackHandler 的子集，直接鸭子类型即可）
    def on_llm_end(self, response, **kwargs) -> None:
        llm_output = getattr(response, "llm_output", None) or {}
        usage = llm_output.get("token_usage") or {}
        if not isinstance(usage, dict) or not usage:
            return
        model = str(llm_output.get("model_name") or "unknown")
        self.tracker.record(
            model=model,
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
        )


def wrap_llm(llm: Any, tracker: Optional[TokenUsageTracker] = None) -> Any:
    """便捷函数：包装 LLM 并返回（缺省 tracker 时新建）。"""
    tracker = tracker or TokenUsageTracker()
    return tracker.wrap(llm)


__all__ = [
    "TokenUsageTracker",
    "UsageCallbackHandler",
    "wrap_llm",
    "_extract_usage_from_response",
]
