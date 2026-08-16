"""运行时监控统计 — UI 无关的纯数据层

职责：记录一个会话内的 LLM / 工具调用指标，并格式化为「状态栏」字符串。
不依赖终端、不依赖 FastAPI；终端和 Web 端都通过相同 API 采集数据、消费结果。

消费者：
    - main.py：终端里每轮结束打印一行类似「4 轮·106 步 | LLM 40m25s | ...」的状态栏
    - 未来 FastAPI WebSocket：调用 to_dict() 后直接推给前端渲染
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


def _fmt_duration(seconds: float) -> str:
    """把秒数格式化为 HhMmSs / MmSs / Xs / Xms，按长度自动取最短表达。"""
    if seconds is None or seconds < 0:
        return "0s"
    ms = round(seconds * 1000)
    if ms < 1000:
        return f"{ms}ms"
    total = round(seconds)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h{m:02d}m{s:02d}s"
    if m:
        return f"{m}m{s:02d}s"
    return f"{s}s"


def _fmt_tokens(n: int) -> str:
    """Token 数的人类可读格式：18.1M、234.5K、1.2K。"""
    if n is None:
        return "0"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


def usage_from_message(msg) -> dict[str, int] | None:
    """从消息提取 token 用量，兼容流式与非流式两种来源。

    流式路径下 langchain 把用量放在 msg.usage_metadata（response_metadata 里没有 token_usage），
    非流式则放在 response_metadata["token_usage"]。统一成 on_llm_end 期望的键。
    """
    um = getattr(msg, "usage_metadata", None) or {}
    if um.get("input_tokens") or um.get("output_tokens"):
        details = um.get("input_token_details") or {}
        return {
            "prompt_tokens": int(um.get("input_tokens") or 0),
            "completion_tokens": int(um.get("output_tokens") or 0),
            "prompt_cache_hit_tokens": int(details.get("cache_read") or 0),
            "prompt_cache_miss_tokens": int(details.get("cache_write") or 0),
        }
    rm = getattr(msg, "response_metadata", None)
    if isinstance(rm, dict) and rm.get("token_usage"):
        return rm["token_usage"]
    return None


@dataclass
class _Timing:
    """单次事件的计时器。"""

    start: float = 0.0
    first_token: float = 0.0  # LLM 首 token 到达时间（相对 start）
    end: float = 0.0

    @property
    def total(self) -> float:
        return self.end - self.start if self.end else time.perf_counter() - self.start


@dataclass
class SessionMetrics:
    """会话级监控指标。每个 session 一个实例。

    用法：
        m = SessionMetrics()
        m.turn_begin()
        m.on_llm_start(); ...; m.on_llm_first_token(); ...; m.on_llm_end(token_usage)
        m.on_tool_start(); ...; m.on_tool_end()
        m.turn_end()
        print(m.status_line())
    """

    # ---- 计数 ----
    turn_count: int = 0           # 用户提问轮数（HumanMessage 数）
    llm_call_count: int = 0       # LLM 实际调用次数（每轮可能多次，如 tool_calls 后再调用）
    tool_call_count: int = 0      # 工具调用次数

    # ---- 累计耗时 ----
    llm_total_time: float = 0.0   # LLM 调用的总耗时（含等待）
    tool_total_time: float = 0.0  # 工具调用的总耗时

    # ---- LLM 性能 ----
    first_token_latencies_ms: list[float] = field(default_factory=list)
    generate_tok_per_s: list[float] = field(default_factory=list)

    # ---- Token & 缓存 ----
    input_tokens: int = 0
    output_tokens: int = 0
    cache_hit_tokens: int = 0
    cache_miss_tokens: int = 0

    # ---- 内部状态：当前正在发生的事件 ----
    _current_turn: bool = False
    _current_llm: _Timing | None = None
    _current_tool: _Timing | None = None

    # =============================================================
    # 埋点钩子
    # =============================================================

    def turn_begin(self) -> None:
        self._current_turn = True

    def turn_end(self) -> None:
        if self._current_turn:
            self.turn_count += 1
            self._current_turn = False

    # ---- LLM ----

    def on_llm_start(self) -> None:
        self._current_llm = _Timing(start=time.perf_counter())

    def on_llm_first_token(self) -> None:
        if self._current_llm and self._current_llm.first_token == 0.0:
            self._current_llm.first_token = time.perf_counter() - self._current_llm.start

    def on_llm_end(self, token_usage: dict[str, Any] | None) -> None:
        """一次 LLM 调用结束。token_usage 为 response_metadata['token_usage']。"""
        if self._current_llm is None:
            return
        self._current_llm.end = time.perf_counter()
        t = self._current_llm
        self._current_llm = None

        self.llm_call_count += 1
        self.llm_total_time += max(0.0, t.total)

        # 首 token 延迟
        if t.first_token > 0:
            self.first_token_latencies_ms.append(t.first_token * 1000)

        # Token 统计
        if token_usage:
            prompt_tokens = int(token_usage.get("prompt_tokens") or 0)
            completion_tokens = int(token_usage.get("completion_tokens") or 0)
            self.input_tokens += prompt_tokens
            self.output_tokens += completion_tokens

            cache_hit = int(token_usage.get("prompt_cache_hit_tokens") or 0)
            cache_miss = int(token_usage.get("prompt_cache_miss_tokens") or 0)
            # DeepSeek 某些版本只返回 hit/miss，不返回 prompt_tokens，兜底换算
            if prompt_tokens == 0 and (cache_hit or cache_miss):
                self.input_tokens += cache_hit + cache_miss
            self.cache_hit_tokens += cache_hit
            self.cache_miss_tokens += cache_miss

            # tok/s：输出 token ÷ 纯生成时长（总耗时 − 首 token 等待）
            generate_time = max(0.001, t.total - (t.first_token if t.first_token > 0 else t.total * 0.5))
            if completion_tokens > 0:
                self.generate_tok_per_s.append(completion_tokens / generate_time)

    # ---- 工具 ----

    def on_tool_start(self) -> None:
        self._current_tool = _Timing(start=time.perf_counter())

    def on_tool_end(self) -> None:
        if self._current_tool is None:
            return
        self._current_tool.end = time.perf_counter()
        self.tool_total_time += max(0.0, self._current_tool.total)
        self._current_tool = None
        self.tool_call_count += 1

    # =============================================================
    # 统计派生值
    # =============================================================

    @property
    def step_count(self) -> int:
        """对齐截图里的「步数」：LLM 调用 + 工具调用"""
        return self.llm_call_count + self.tool_call_count

    @property
    def avg_first_token_ms(self) -> float | None:
        return (sum(self.first_token_latencies_ms) / len(self.first_token_latencies_ms)) if self.first_token_latencies_ms else None

    @property
    def avg_tok_per_s(self) -> float | None:
        return (sum(self.generate_tok_per_s) / len(self.generate_tok_per_s)) if self.generate_tok_per_s else None

    @property
    def cache_hit_rate(self) -> float | None:
        total = self.cache_hit_tokens + self.cache_miss_tokens
        if total <= 0:
            return None
        return self.cache_hit_tokens / total

    # =============================================================
    # 格式化：对齐你截图里的样式
    # =============================================================

    def status_line(self) -> str:
        """返回一行状态栏：`4 轮·106 步 | LLM 40m25s·工具 29m21s | 首 token 1.7s·80 tok/s | 缓存命中 99% | 输入 18.1M tok·输出 1.2M tok`"""
        parts = []
        # 轮数·步数（进行中的这轮 turn_end 前 +1）
        turns = self.turn_count + (1 if self._current_turn else 0)
        parts.append(f"{turns} 轮 · {self.step_count} 步")

        # LLM·工具总耗时
        parts.append(f"LLM {_fmt_duration(self.llm_total_time)} · 工具 {_fmt_duration(self.tool_total_time)}")

        # 首 token 延迟 · tok/s
        perf = []
        if self.avg_first_token_ms is not None:
            s = self.avg_first_token_ms / 1000
            perf.append(f"首 token 平均 {s:.1f}s" if s >= 1 else f"首 token 平均 {self.avg_first_token_ms:.0f}ms")
        if self.avg_tok_per_s is not None:
            perf.append(f"{self.avg_tok_per_s:.0f} tok/s")
        if perf:
            parts.append(" · ".join(perf))

        # 缓存命中率（无数据时显示 —）
        if self.cache_hit_rate is not None:
            parts.append(f"缓存命中 {self.cache_hit_rate * 100:.0f}%")
        else:
            parts.append("缓存命中 —%")

        # 输入·输出 token
        parts.append(f"输入 {_fmt_tokens(self.input_tokens)} tok · 输出 {_fmt_tokens(self.output_tokens)} tok")

        return "  |  ".join(parts)

    # =============================================================
    # 序列化：给 FastAPI WebSocket 推前端
    # =============================================================

    def to_dict(self) -> dict:
        return {
            "turn_count": self.turn_count,
            "step_count": self.step_count,
            "llm_call_count": self.llm_call_count,
            "tool_call_count": self.tool_call_count,
            "llm_total_time_sec": round(self.llm_total_time, 2),
            "tool_total_time_sec": round(self.tool_total_time, 2),
            "avg_first_token_ms": round(self.avg_first_token_ms, 1) if self.avg_first_token_ms else None,
            "avg_tok_per_s": round(self.avg_tok_per_s, 1) if self.avg_tok_per_s else None,
            "cache_hit_rate_pct": round(self.cache_hit_rate * 100, 1) if self.cache_hit_rate is not None else None,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_hit_tokens": self.cache_hit_tokens,
            "cache_miss_tokens": self.cache_miss_tokens,
            "status_line": self.status_line(),
        }
