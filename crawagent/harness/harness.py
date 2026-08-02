"""CrawlHarness — pi 风格的 Agent Harness 编排层

拥有 session + hooks + lanes。
管理生命周期：创建 session → 注册 hooks/tools → 创建 lanes → 运行 loop。

参考 pi 的 AgentHarness：
- phase 管理：idle → turn → compact
- turn snapshot：每轮不可变
- save point：轮间刷新状态
- lanes：main/monitor/security 并行
- 操作方法返回 RunResult 而非抛异常
"""
from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional

from crawagent.harness.types import (
    Phase, HookEvent, CrawlMessage, TurnSnapshot, RunResult,
    OperationType, LaneInfo, CrawlToolDef, TokenUsage,
)
from crawagent.harness.session import CrawlSession, SessionManager
from crawagent.harness.hooks import CrawlHooks
from crawagent.harness.loop import CrawlLoop
from crawagent.config.settings import get_settings


def _usage_to_dict(u: TokenUsage) -> Dict[str, Any]:
    """TokenUsage → dict（含命中率与费用，供 API/前端展示）。"""
    return {
        "prompt_tokens": u.prompt_tokens,
        "completion_tokens": u.completion_tokens,
        "total_tokens": u.total_tokens,
        "cache_hit_tokens": u.cache_hit_tokens,
        "cache_miss_tokens": u.cache_miss_tokens,
        "hit_rate": u.hit_rate,
        "cost_yuan": u.cost_yuan(),
        "model": u.model,
    }


class CrawlHarness:
    """pi 风格的 Agent Harness（编排层）
    
    拥有 session + hooks + lanes。
    管理生命周期：创建 session → 注册 hooks/tools → 创建 lanes → 运行 loop。
    
    参考 pi 的 AgentHarness：
    - phase 管理：idle → turn → compact
    - turn snapshot：每轮不可变
    - save point：轮间刷新状态
    - lanes：main/monitor/security 并行
    - 操作方法返回 RunResult 而非抛异常
    """
    
    def __init__(
        self,
        session_manager: SessionManager,
        hooks: Optional[CrawlHooks] = None,
        tools: Optional[List[CrawlToolDef]] = None,
        tool_executors: Optional[Dict[str, Any]] = None,
    ):
        self._session_manager = session_manager
        self.hooks = hooks or CrawlHooks()
        self._tools = tools or []
        self._tool_executors: Dict[str, Any] = tool_executors or {}
        self._sessions: Dict[str, CrawlSession] = {}  # session_id → session
        self._loops: Dict[str, CrawlLoop] = {}  # session_id → active loop
        self._session_usage: Dict[str, TokenUsage] = {}  # session_id → 累计 token 用量
        self._phase: Phase = Phase.IDLE
    
    # ---- Session 管理 ----
    
    async def create_session(self, name: str = "") -> CrawlSession:
        """创建新 session"""
        session = await self._session_manager.create_session(name)
        self._sessions[session.session_id] = session
        
        # 创建 default main lane
        await session.create_lane("main")
        
        return session
    
    async def get_session(self, session_id: str) -> Optional[CrawlSession]:
        """获取已有 session"""
        if session_id in self._sessions:
            return self._sessions[session_id]
        session = await self._session_manager.get_session(session_id)
        if session:
            self._sessions[session_id] = session
        return session
    
    # ---- Lane 管理 ----
    
    async def create_lane(self, session_id: str, name: str, at_entry_id: str = None) -> LaneInfo:
        """创建 lane"""
        session = await self.get_session(session_id)
        if not session:
            raise ValueError(f"Session not found: {session_id}")
        return await session.create_lane(name, at_entry_id)
    
    async def list_lanes(self, session_id: str) -> List[LaneInfo]:
        """列出 lanes"""
        session = await self.get_session(session_id)
        if not session:
            return []
        return await session.list_lanes()
    
    # ---- Tool 管理 ----
    
    def set_tools(self, tools: List[CrawlToolDef]) -> None:
        """设置工具集"""
        self._tools = tools
    
    def get_tools(self) -> List[CrawlToolDef]:
        """获取工具集"""
        return self._tools
    
    def register_tool_executor(self, session_id: str, tool_name: str, executor) -> None:
        """注册工具执行器到指定 session 的 loop"""
        loop = self._loops.get(session_id)
        if loop:
            loop.register_tool_executor(tool_name, executor)
    
    # ---- 运行 ----
    
    async def prompt(
        self,
        session_id: str,
        message: str,
        lane_name: str = "main",
        max_turns: int | None = None,
        task_notes: str = "",
    ) -> RunResult:
        """用户输入 → Agent Loop 运行 → 返回结果

        这是主要交互入口。

        Args:
            session_id: 会话 ID
            message: 用户消息
            lane_name: lane 名称，默认 main
            max_turns: 本次 prompt 允许的最大 LLM turn 数，None 则取 settings 默认
            task_notes: 任务备注（如 max_pages/max_depth 预算），注入系统提示词
        """
        session = await self.get_session(session_id)
        if not session:
            return RunResult.from_error(f"Session not found: {session_id}")

        self._phase = Phase.TURN

        # 创建 loop
        settings = get_settings()
        loop = CrawlLoop(
            session=session,
            hooks=self.hooks,
            tools=self._tools,
            max_turns=max_turns if max_turns is not None else settings.harness_max_turns,
            compaction_threshold=settings.harness_compaction_threshold,
            tool_executors=self._tool_executors,
            task_notes=task_notes,
        )
        self._loops[session_id] = loop
        
        try:
            result = await loop.run(message, lane_name)
            self._phase = Phase.IDLE

            # 记录本次与累计 token 用量到 result.data（前端展示 token/命中率/费用）
            loop_usage = loop.token_usage
            if result.data is None:
                result.data = {}
            result.data["usage"] = _usage_to_dict(loop_usage)
            # 会话累计（内存；多次 prompt 同一 session 时累加）
            prev = self._session_usage.get(session_id)
            if prev is None:
                prev = TokenUsage()
                self._session_usage[session_id] = prev
            prev.prompt_tokens += loop_usage.prompt_tokens
            prev.completion_tokens += loop_usage.completion_tokens
            prev.total_tokens += loop_usage.total_tokens
            prev.cache_hit_tokens += loop_usage.cache_hit_tokens
            prev.cache_miss_tokens += loop_usage.cache_miss_tokens
            prev.model = loop_usage.model or prev.model
            result.data["usage_total"] = _usage_to_dict(prev)
            return result
        except Exception as e:
            self._phase = Phase.IDLE
            return RunResult.from_error(str(e))

    def get_session_usage(self, session_id: str) -> TokenUsage:
        """获取某 session 的累计 token 用量（含缓存字段）。"""
        return self._session_usage.get(session_id, TokenUsage())
    
    async def stop(self, session_id: str) -> None:
        """停止指定 session 的 loop"""
        loop = self._loops.get(session_id)
        if loop:
            await loop.stop()
    
    # ---- 状态查询 ----
    
    @property
    def phase(self) -> Phase:
        return self._phase
    
    def get_current_snapshot(self, session_id: str) -> Optional[TurnSnapshot]:
        """获取当前 turn snapshot"""
        loop = self._loops.get(session_id)
        if loop:
            return loop._current_snapshot
        return None
    
    # ---- 生命周期 ----
    
    async def close(self) -> None:
        """关闭所有资源"""
        await self._session_manager.close()
        self._sessions.clear()
        self._loops.clear()
    
    # ---- 便捷方法 ----
    
    async def quick_crawl(self, url: str, instruction: str = "") -> RunResult:
        """快速爬取：自动创建 session + 运行 + 返回结果

        简化的单次调用入口，适合 CLI / API 直接使用。
        session_id 会写入 result.data，便于前端加载历史。
        """
        session = await self.create_session(name=f"crawl:{url[:50]}")
        message = instruction or f"爬取 {url} 的内容"
        if url:
            # 将 URL 注入到消息中
            message = f"{message}\n\n目标URL: {url}"

        result = await self.prompt(session.session_id, message)
        # 把 session_id 带回去，便于前端展示历史
        if result.data is None:
            result.data = {}
        result.data["session_id"] = session.session_id
        return result
