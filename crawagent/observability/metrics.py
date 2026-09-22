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

    DeepSeek 缓存说明：
    DeepSeek 在 token_usage 顶层返回 prompt_cache_hit_tokens / prompt_cache_miss_tokens。
    langchain-openai 会把 hit 映射到 input_token_details.cache_read。
    cache miss 数据不在 input_token_details 中，只在 response_metadata.token_usage 中。

    为兼容不同 langchain 版本，我们同时从 usage_metadata 和 response_metadata 取值。
    优先使用 response_metadata.token_usage 作为权威数据源。
    """
    um = getattr(msg, "usage_metadata", None) or {}
    rm = getattr(msg, "response_metadata", None)

    hit = 0
    miss = 0

    # ---- 从 usage_metadata.input_token_details 取 cache_read ----
    if um.get("input_tokens") or um.get("output_tokens"):
        details = um.get("input_token_details") or {}
        hit = int(details.get("cache_read") or 0)

    # ---- 从 response_metadata.token_usage 取权威数据 ----
    if isinstance(rm, dict):
        tu = rm.get("token_usage") or {}
        tu_hit = tu.get("prompt_cache_hit_tokens")
        tu_miss = tu.get("prompt_cache_miss_tokens")
        if tu_hit is not None:
            hit = int(tu_hit)
        if tu_miss is not None:
            miss = int(tu_miss)
        # 兜底：某些版本 API 用 prompt_tokens_details.cached_tokens
        if miss == 0:
            ptd = tu.get("prompt_tokens_details") or {}
            cached = ptd.get("cached_tokens")
            if cached is not None:
                miss = int(cached)

    # DeepSeek 语义：prompt_tokens = cache_hit + cache_miss。
    # 部分 langchain 版本不映射 prompt_cache_miss_tokens（token_usage 为空），
    # 此时 miss 可由 prompt - hit 推导，避免命中率虚高成 100%。
    prompt_total = int((um.get("input_tokens") if um else 0) or 0)
    if prompt_total and miss == 0 and hit < prompt_total:
        miss = prompt_total - hit

    if um.get("input_tokens") or um.get("output_tokens"):
        return {
            "prompt_tokens": int(um.get("input_tokens") or 0),
            "completion_tokens": int(um.get("output_tokens") or 0),
            "prompt_cache_hit_tokens": hit,
            "prompt_cache_miss_tokens": miss,
        }
    if isinstance(rm, dict) and rm.get("token_usage"):
        tu = rm["token_usage"]
        return {
            "prompt_tokens": int(tu.get("prompt_tokens") or 0),
            "completion_tokens": int(tu.get("completion_tokens") or 0),
            "prompt_cache_hit_tokens": hit,
            "prompt_cache_miss_tokens": miss,
        }
    return None


@dataclass
class _Timing:
    """单次事件的计时器。"""

    start: float = 0.0
    first_token: float = 0.0  # LLM 首 token 到达时间（相对 start）
    end: float = 0.0

    @property
    def total(self) -> float:
        """已结束的 ``_Timing`` 返回 ``end - start``；未结束则返回当前累计。

        Returns:
            耗时（秒）。
        """
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
    # 最近一次 LLM 调用的缓存数据（区分"会话累计"与"最近一轮"，避免冷启动/挤占误判）
    last_hit_tokens: int = 0
    last_miss_tokens: int = 0

    # ---- 内部状态：当前正在发生的事件 ----
    _current_turn: bool = False
    _current_llm: _Timing | None = None
    _current_tool: _Timing | None = None

    # =============================================================
    # 埋点钩子
    # =============================================================

    def turn_begin(self) -> None:
        """标记新一轮开始（``_current_turn = True``）；与 ``turn_end`` 配对。"""
        self._current_turn = True

    def turn_end(self) -> None:
        """结束当前轮并 ``turn_count += 1``；只在 ``turn_begin`` 后调用才生效。"""
        if self._current_turn:
            self.turn_count += 1
            self._current_turn = False

    # ---- LLM ----

    def on_llm_start(self) -> None:
        """记录一次 LLM 调用的开始时间（开 ``_Timing``）。"""
        self._current_llm = _Timing(start=time.perf_counter())

    def on_llm_first_token(self) -> None:
        """记录 LLM 首 token 相对 ``on_llm_start`` 的延迟（仅记录首次）。"""
        if self._current_llm and self._current_llm.first_token == 0.0:
            self._current_llm.first_token = time.perf_counter() - self._current_llm.start

    def on_llm_end(self, token_usage: dict[str, Any] | None) -> None:
        """一次 LLM 调用结束。token_usage 为 usage_from_message() 的结果。"""
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
            if prompt_tokens == 0 and (cache_hit or cache_miss):
                self.input_tokens += cache_hit + cache_miss
            self.cache_hit_tokens += cache_hit
            self.cache_miss_tokens += cache_miss
            self.last_hit_tokens = cache_hit
            self.last_miss_tokens = cache_miss
            
            # DEBUG: 打印本次 LLM 调用的缓存数据
            print(f"[METRICS] on_llm_end: prompt={prompt_tokens}, completion={completion_tokens}, "
                  f"cache_hit={cache_hit}, cache_miss={cache_miss}, "
                  f"total_input={self.input_tokens}, total_hit={self.cache_hit_tokens}, "
                  f"total_miss={self.cache_miss_tokens}")

            # tok/s：输出 token ÷ 纯生成时长（总耗时 − 首 token 等待）
            generate_time = max(0.001, t.total - (t.first_token if t.first_token > 0 else t.total * 0.5))
            if completion_tokens > 0:
                self.generate_tok_per_s.append(completion_tokens / generate_time)

    # ---- 工具 ----

    def on_tool_start(self) -> None:
        """记录一次工具调用的开始时间（开 ``_Timing``）。"""
        self._current_tool = _Timing(start=time.perf_counter())

    def on_tool_end(self) -> None:
        """结束工具调用计时：累加 ``tool_total_time`` + ``tool_call_count += 1``。无开始事件时 no-op。"""
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
        """所有 LLM 调用的平均首 token 延迟（毫秒）；无样本时返回 None。"""
        return (sum(self.first_token_latencies_ms) / len(self.first_token_latencies_ms)) if self.first_token_latencies_ms else None

    @property
    def avg_tok_per_s(self) -> float | None:
        """平均生成速度（tokens/秒）；无样本时返回 None。"""
        return (sum(self.generate_tok_per_s) / len(self.generate_tok_per_s)) if self.generate_tok_per_s else None

    @property
    def cache_hit_rate(self) -> float | None:
        """prompt cache 命中率（0.0-1.0）；无缓存数据时返回 None。"""
        total = self.cache_hit_tokens + self.cache_miss_tokens
        if total <= 0:
            return None
        return self.cache_hit_tokens / total

    @property
    def last_round_hit_rate(self) -> float | None:
        """最近一次 LLM 调用的命中率（排除冷启动/挤占的一次性影响）。"""
        total = self.last_hit_tokens + self.last_miss_tokens
        if total <= 0:
            return None
        return self.last_hit_tokens / total

    # =============================================================
    # 格式化：对齐你截图里的样式
    # =============================================================

    def status_line(self) -> str:
        """返回一行状态栏：`4 轮·106 步 | LLM 40m25s·工具 29m21s | 首 token 1.7s·80 tok/s | 缓存命中 99% | 输入 18.1M tok·输出 1.2M tok`"""
        parts = []
        turns = self.turn_count + (1 if self._current_turn else 0)
        parts.append(f"{turns} 轮 · {self.step_count} 步")

        parts.append(f"LLM {_fmt_duration(self.llm_total_time)} · 工具 {_fmt_duration(self.tool_total_time)}")

        perf = []
        if self.avg_first_token_ms is not None:
            s = self.avg_first_token_ms / 1000
            perf.append(f"首 token 平均 {s:.1f}s" if s >= 1 else f"首 token 平均 {self.avg_first_token_ms:.0f}ms")
        if self.avg_tok_per_s is not None:
            perf.append(f"{self.avg_tok_per_s:.0f} tok/s")
        if perf:
            parts.append(" · ".join(perf))

        if self.cache_hit_rate is not None:
            cache_part = f"缓存命中 {self.cache_hit_rate * 100:.0f}%"
            # 本轮命中率：冷启动/缓存挤占只影响累计值，最近一轮才是前缀稳定性的真实反映
            if self.last_round_hit_rate is not None and self.llm_call_count > 1:
                cache_part += f"（本轮 {self.last_round_hit_rate * 100:.0f}%）"
            parts.append(cache_part)
        else:
            parts.append("缓存命中 —%")

        parts.append(f"输入 {_fmt_tokens(self.input_tokens)} tok · 输出 {_fmt_tokens(self.output_tokens)} tok")

        return "  |  ".join(parts)

    # =============================================================
    # 序列化：给 FastAPI WebSocket 推前端
    # =============================================================

    def to_dict(self) -> dict:
        """序列化监控指标为 dict（用于 FastAPI WebSocket 推前端 / 持久化）。

        包含派生指标（``avg_first_token_ms`` / ``avg_tok_per_s`` / ``cache_hit_rate``）
        和原始列表（``first_token_latencies_ms`` / ``generate_tok_per_s``），便于恢复。

        Returns:
            字段名 → 数值的 dict，可直接 ``json.dumps``。
        """
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
            "last_round_hit_rate_pct": round(self.last_round_hit_rate * 100, 1) if self.last_round_hit_rate is not None else None,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_hit_tokens": self.cache_hit_tokens,
            "cache_miss_tokens": self.cache_miss_tokens,
            "last_hit_tokens": self.last_hit_tokens,
            "last_miss_tokens": self.last_miss_tokens,
            # 原始列表：持久化后能恢复 avg_first_token_ms / avg_tok_per_s 派生值
            "first_token_latencies_ms": list(self.first_token_latencies_ms),
            "generate_tok_per_s": list(self.generate_tok_per_s),
            "status_line": self.status_line(),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "SessionMetrics":
        """从持久化的 dict 重建 SessionMetrics（server 重启后恢复状态栏）。

        丢弃 _current_turn / _current_llm / _current_tool 等瞬时状态——
        恢复的是已完成轮次的累计数据，不恢复进行中的轮次。
        """
        m = cls()
        m.turn_count = int(data.get("turn_count") or 0)
        m.llm_call_count = int(data.get("llm_call_count") or 0)
        m.tool_call_count = int(data.get("tool_call_count") or 0)
        m.llm_total_time = float(data.get("llm_total_time_sec") or 0)
        m.tool_total_time = float(data.get("tool_total_time_sec") or 0)
        m.input_tokens = int(data.get("input_tokens") or 0)
        m.output_tokens = int(data.get("output_tokens") or 0)
        m.cache_hit_tokens = int(data.get("cache_hit_tokens") or 0)
        m.cache_miss_tokens = int(data.get("cache_miss_tokens") or 0)
        m.last_hit_tokens = int(data.get("last_hit_tokens") or 0)
        m.last_miss_tokens = int(data.get("last_miss_tokens") or 0)
        latencies = data.get("first_token_latencies_ms")
        if isinstance(latencies, list):
            m.first_token_latencies_ms = [float(x) for x in latencies]
        tps = data.get("generate_tok_per_s")
        if isinstance(tps, list):
            m.generate_tok_per_s = [float(x) for x in tps]
        return m
