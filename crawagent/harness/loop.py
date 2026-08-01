"""pi Agent Harness - CrawlLoop (driverLoop 的 Python 实现)

本模块实现了 Agent 的核心执行循环，参考 pi Agent Harness 的 driverLoop 设计。

核心循环流程：
1. checkpoint → 创建不可变 TurnSnapshot
2. LLM 调用 → 获取助手响应
3. 如果有 tool_calls → 三阶段执行（prepare → execute → finalize）
4. 如果有 followUp → 继续循环
5. 否则 → finish

三阶段工具执行：
- Phase 1 (prepare): 校验工具定义 + hook 拦截
- Phase 2 (execute): 实际调用工具执行器
- Phase 3 (finalize): 补充 tool_call_id 等结果字段
"""
from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any, Dict, List, Optional, Callable, Awaitable

from crawagent.harness.types import (
    Phase, HookEvent, CrawlMessage, TurnSnapshot, RunResult,
    OperationType, TokenUsage, ToolExecMode, CrawlToolDef,
)
from crawagent.harness.session import CrawlSession
from crawagent.harness.hooks import CrawlHooks


class CrawlLoop:
    """pi 风格的 Agent Loop（driverLoop）

    核心循环：
    1. checkpoint → 创建不可变 TurnSnapshot
    2. LLM 调用 → 获取助手响应
    3. 如果有 tool_calls → 三阶段执行（prepare → execute → finalize）
    4. 如果有 followUp → 继续
    5. 否则 → finish
    """

    def __init__(
        self,
        session: CrawlSession,
        hooks: CrawlHooks,
        tools: List[CrawlToolDef],
        max_turns: int = 50,
        compaction_threshold: int = 80000,
        tool_executors: Optional[Dict[str, Callable]] = None,
    ):
        self.session = session
        self.hooks = hooks
        self.tools = tools
        self.max_turns = max_turns
        self.compaction_threshold = compaction_threshold

        self._tool_map: Dict[str, CrawlToolDef] = {t.name: t for t in tools}
        self._tool_executors: Dict[str, Callable] = {}  # name → async callable
        # 初始化时注入已注册的执行器
        if tool_executors:
            for name, executor in tool_executors.items():
                self._tool_executors[name] = executor
        self._current_snapshot: Optional[TurnSnapshot] = None
        self._phase: Phase = Phase.IDLE
        self._turn_count: int = 0
        self._token_usage: TokenUsage = TokenUsage(
            prompt_tokens=0, completion_tokens=0, total_tokens=0, model=""
        )
        self._should_stop: bool = False
        self._compaction_threshold = compaction_threshold
        self._lane_name: str = "main"

    def register_tool_executor(self, tool_name: str, executor: Callable) -> None:
        """注册工具执行器（async callable）"""
        self._tool_executors[tool_name] = executor

    async def run(self, user_message: str, lane_name: str = "main") -> RunResult:
        """运行 Agent Loop 直到结束

        1. 创建 checkpoint
        2. 添加用户消息到 session
        3. 进入循环
        4. 返回 RunResult
        """
        self._lane_name = lane_name
        self._should_stop = False
        self._turn_count = 0

        # 开始 operation
        op_id = await self.session.start_operation(lane_name, OperationType.RUN)

        try:
            # 添加用户消息
            user_entry = CrawlMessage(
                id=uuid.uuid4().hex[:24],
                parent_id=await self._get_parent_id(),
                role="user",
                content=user_message,
            )
            await self.session.append_entry(user_entry)
            await self.session.add_operation_entry(op_id, user_entry.id)

            # fire BEFORE_RUN hook
            context: Dict[str, Any] = {"user_message": user_message, "lane": lane_name}
            context = await self.hooks.fire(HookEvent.BEFORE_RUN, context)

            # 主循环
            while not self._should_stop and self._turn_count < self.max_turns:
                self._turn_count += 1

                # 1. Checkpoint
                await self._checkpoint()

                # 2. LLM 调用
                assistant_msg = await self._step()

                if assistant_msg is None:
                    # 没有助手消息，结束
                    break

                # 3. 如果有 tool_calls
                if assistant_msg.tool_calls:
                    # fire BEFORE_TOOL hook
                    tool_context: Dict[str, Any] = {
                        "tool_calls": assistant_msg.tool_calls,
                        "lane": self._lane_name,
                    }
                    tool_context = await self.hooks.fire(HookEvent.BEFORE_TOOL, tool_context)

                    # 执行工具
                    tool_results = await self._execute_tool_batch(
                        tool_context.get("tool_calls", assistant_msg.tool_calls)
                    )

                    # fire AFTER_TOOL hook
                    after_context: Dict[str, Any] = {"tool_results": tool_results, "lane": self._lane_name}
                    after_context = await self.hooks.fire(HookEvent.AFTER_TOOL, after_context)

                    # 添加工具结果到 session
                    for result in tool_results:
                        tool_entry = CrawlMessage(
                            id=uuid.uuid4().hex[:24],
                            parent_id=assistant_msg.id,
                            role="tool",
                            content=result.get("content", ""),
                            tool_call_id=result.get("tool_call_id"),
                        )
                        await self.session.append_entry(tool_entry)
                        await self.session.add_operation_entry(op_id, tool_entry.id)
                        # 更新 lane leaf 指针（F5 修复：对话树真实化）
                        await self.session.update_lane_leaf(self._lane_name, tool_entry.id)

                    continue  # 新 checkpoint

                # 4. 没有工具调用，检查 followUp
                follow_up = self._extract_follow_up(assistant_msg)
                if follow_up:
                    continue

                # 5. 无 followUp，结束
                break

            # fire BEFORE_RUN_END hook
            end_context: Dict[str, Any] = {"turn_count": self._turn_count, "lane": self._lane_name}
            end_context = await self.hooks.fire(HookEvent.BEFORE_RUN_END, end_context)

            follow_up = end_context.get("follow_up")
            if follow_up:
                return RunResult.completed(data={"follow_up": follow_up})

            return RunResult.completed(data={"turns": self._turn_count})

        except Exception as e:
            return RunResult.from_error(str(e))

        finally:
            await self.session.finish_operation(op_id)

    async def stop(self) -> None:
        """请求停止循环"""
        self._should_stop = True

    # ---- 内部方法 ----

    async def _checkpoint(self) -> None:
        """创建 checkpoint：刷新写入 + 创建新 TurnSnapshot + 触发 Compaction"""
        self._phase = Phase.TURN

        entries = await self.session.get_entries(limit=200)
        tool_names = list(self._tool_map.keys())

        # F6: Compaction 接入 — token 超阈值时压缩早期消息
        try:
            from crawagent.harness.compaction import Compaction
            compaction = Compaction(
                session=self.session,
                hooks=self.hooks,
                threshold=self._compaction_threshold,
                keep_recent=10,
            )
            if await compaction.should_compact(entries):
                from loguru import logger
                logger.info(
                    f"[Compaction] 触发压缩 (tokens≈{compaction.estimate_tokens(entries)})"
                )
                summary = await compaction.compact(entries, lane_name=self._lane_name)
                if summary is not None:
                    # 重新读取：summary + 最近消息
                    entries = await self.session.get_entries(limit=200)
        except Exception as e:
            from loguru import logger
            logger.debug(f"[Compaction] 跳过（{type(e).__name__}: {e}）")

        self._current_snapshot = TurnSnapshot(
            turn_id=uuid.uuid4().hex[:24],
            phase=self._phase,
            messages=list(entries),  # 不可变快照
            tools=tool_names,
            system_prompt="",  # 由 system_prompt.py 组装
            lane_name=self._lane_name,
            created_at=time.time(),
        )

    async def _step(self) -> Optional[CrawlMessage]:
        """单步 LLM 调用

        1. 组装上下文（从 session 获取消息列表，按时间正序）
        2. 组装系统提示词
        3. 调用 LLM
        4. 解析响应
        5. 追加助手消息到 session
        """
        # 获取上下文（默认 asc 正序：老→新，保证 tool 消息紧跟其 tool_calls）
        entries = await self.session.get_entries(limit=100)

        # 组装系统提示词（F2 修复：真正调用 assemble）
        from crawagent.harness.system_prompt import SystemPromptAssembler
        prompt_assembler = SystemPromptAssembler()
        system_prompt = prompt_assembler.assemble(tools=self.tools)

        # 将 CrawlMessage 转换为 LangChain Message 对象（含 tool_calls/tool_call_id）
        from langchain_core.messages import (
            SystemMessage, HumanMessage, AIMessage, ToolMessage,
        )

        messages: List[Any] = []
        if system_prompt:
            messages.append(SystemMessage(content=system_prompt))
        for e in entries:
            if e.role == "user":
                messages.append(HumanMessage(content=e.content))
            elif e.role == "assistant":
                tcs = None
                if e.tool_calls:
                    # 转为 LangChain ToolCall 格式
                    tcs = []
                    for tc in e.tool_calls:
                        if isinstance(tc, dict) and "function" in tc:
                            # OpenAI 格式: {id, type, function: {name, arguments}}
                            args = tc["function"].get("arguments", {})
                            if isinstance(args, str):
                                import json as _json
                                try:
                                    args = _json.loads(args)
                                except Exception:
                                    args = {}
                            tcs.append({
                                "name": tc["function"].get("name", ""),
                                "args": args,
                                "id": tc.get("id", e.tool_call_id or ""),
                                "type": "tool_call",
                            })
                        elif isinstance(tc, dict) and "name" in tc:
                            tcs.append({
                                "name": tc.get("name", ""),
                                "args": tc.get("args", tc.get("arguments", {})),
                                "id": tc.get("id", e.tool_call_id or ""),
                                "type": "tool_call",
                            })
                messages.append(AIMessage(
                    content=e.content,
                    tool_calls=tcs if tcs else None,
                ))
            elif e.role == "tool":
                messages.append(ToolMessage(
                    content=e.content,
                    tool_call_id=e.tool_call_id or "",
                ))

        # 获取 LLM（延迟导入避免循环引用）
        from crawagent.llm.factory import get_llm
        from crawagent.harness.tools import to_langchain_tools

        # 将工具定义转为 LangChain 格式并绑定到 LLM，使模型能生成 tool_calls
        llm_tools = to_langchain_tools(self.tools)
        llm = get_llm(tools=llm_tools) if llm_tools else get_llm()

        # fire BEFORE_REQUEST hook
        req_context: Dict[str, Any] = {"messages": messages, "lane": self._lane_name}
        req_context = await self.hooks.fire(HookEvent.BEFORE_REQUEST, req_context)

        try:
            response = await llm.ainvoke(req_context.get("messages", messages))
        except Exception as e:
            # LLM 调用失败
            from loguru import logger
            logger.error(f"LLM 调用失败: {type(e).__name__}: {e}")
            return None

        # fire AFTER_RESPONSE hook
        resp_context: Dict[str, Any] = {"response": response, "lane": self._lane_name}
        resp_context = await self.hooks.fire(HookEvent.AFTER_RESPONSE, resp_context)

        # 解析响应
        content = response.content if hasattr(response, 'content') else str(response)
        tool_calls: List[Dict[str, Any]] = []
        if hasattr(response, 'tool_calls') and response.tool_calls:
            tool_calls = [
                {
                    "id": tc.get("id", uuid.uuid4().hex[:24]) if isinstance(tc, dict) else uuid.uuid4().hex[:24],
                    "type": "function",
                    "function": {
                        "name": tc.get("name", "") if isinstance(tc, dict) else getattr(tc, "name", ""),
                        "arguments": tc.get("args", {}) if isinstance(tc, dict) else getattr(tc, "args", {}),
                    },
                }
                for tc in response.tool_calls
            ]

        # 追加助手消息
        parent_id = await self._get_parent_id()
        assistant_entry = CrawlMessage(
            id=uuid.uuid4().hex[:24],
            parent_id=parent_id,
            role="assistant",
            content=content,
            tool_calls=tool_calls,
        )
        await self.session.append_entry(assistant_entry)

        # 更新 lane leaf 指针（F5 修复：对话树真实化）
        await self.session.update_lane_leaf(self._lane_name, assistant_entry.id)

        return assistant_entry

    async def _execute_tool_batch(self, tool_calls: List[Dict]) -> List[Dict]:
        """批量执行工具调用（三阶段执行）

        Phase 1: prepare — 校验 + hook
        Phase 2: execute — 实际执行
        Phase 3: finalize — patch 结果

        当所有工具均为 PARALLEL 模式时并行执行，否则顺序执行。
        """
        results: List[Dict[str, Any]] = []

        # 判断执行模式：如果存在 PARALLEL 工具且有多于一个调用，则并行执行
        has_parallel = any(
            self._tool_map.get(tc["function"]["name"]) is not None
            and self._tool_map[tc["function"]["name"]].exec_mode == ToolExecMode.PARALLEL
            for tc in tool_calls
        )

        if has_parallel and len(tool_calls) > 1:
            # 并行执行
            tasks = [self._execute_single_tool(tc) for tc in tool_calls]
            raw_results = await asyncio.gather(*tasks, return_exceptions=True)
            results = [
                r if not isinstance(r, Exception) else {"error": str(r)}
                for r in raw_results
            ]
        else:
            # 顺序执行
            for tc in tool_calls:
                result = await self._execute_single_tool(tc)
                results.append(result)

        return results

    async def _execute_single_tool(self, tool_call: Dict) -> Dict:
        """执行单个工具调用（三阶段）

        Phase 1 (Prepare): 校验工具定义是否存在
        Phase 2 (Execute): 调用已注册的执行器
        Phase 3 (Finalize): 补充 tool_call_id，统一返回格式
        """
        tool_name = tool_call["function"]["name"]
        tool_args = tool_call["function"].get("arguments", {})
        tool_call_id = tool_call.get("id", "")

        # Phase 1: Prepare
        tool_def = self._tool_map.get(tool_name)
        if not tool_def:
            return {
                "tool_call_id": tool_call_id,
                "content": f"Error: Unknown tool '{tool_name}'",
                "error": True,
            }

        # Phase 2: Execute
        executor = self._tool_executors.get(tool_name)
        if not executor:
            return {
                "tool_call_id": tool_call_id,
                "content": f"Error: No executor for tool '{tool_name}'",
                "error": True,
            }

        try:
            result = await executor(tool_args)
        except Exception as e:
            return {
                "tool_call_id": tool_call_id,
                "content": f"Tool execution error: {e}",
                "error": True,
            }

        # Phase 3: Finalize
        if isinstance(result, dict):
            result["tool_call_id"] = tool_call_id
            return result
        else:
            return {
                "tool_call_id": tool_call_id,
                "content": str(result),
            }

    def _extract_follow_up(self, msg: CrawlMessage) -> Optional[str]:
        """从助手消息中提取 followUp 指令

        简单实现：如果 content 包含 [FOLLOW_UP:xxx]，提取 xxx
        """
        content = msg.content
        if "[FOLLOW_UP:" in content:
            start = content.index("[FOLLOW_UP:") + len("[FOLLOW_UP:")
            end = content.index("]", start)
            return content[start:end]
        return None

    async def _get_parent_id(self) -> Optional[str]:
        """获取当前 lane 的 parent entry id"""
        leaf = await self.session.get_leaf_entry(self._lane_name)
        return leaf.id if leaf else None
