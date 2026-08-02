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
        task_notes: str = "",
    ):
        self.session = session
        self.hooks = hooks
        self.tools = tools
        self.max_turns = max_turns
        self.compaction_threshold = compaction_threshold
        self._task_notes = task_notes

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
        self._compaction: Optional[Any] = None  # 复用实例，保留压缩间隔状态

        # 缓存稳定前缀（对齐 Reasonix cache-stable prefix）：
        # system prompt 构造一次，全生命周期字节级复用；动态内容（task_notes）
        # 走 transient turn-injection 放消息尾部，永不触碰此前缀。
        from crawagent.harness.system_prompt import SystemPromptAssembler
        self._system_prompt = SystemPromptAssembler().assemble(tools=self.tools)

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
            # F9: 崩溃恢复 — 清理上一次崩溃遗留的 open operation（孤儿 tool_calls 等）
            try:
                recovery_plan = await self.session.build_recovery_plan(hooks=self.hooks)
                if recovery_plan.operations or recovery_plan.open_lanes:
                    from loguru import logger
                    logger.warning(
                        f"[Loop] 检测到 {len(recovery_plan.operations)} 个遗留 open operation，"
                        f"执行崩溃恢复: {[r.action.value for r in recovery_plan.operations]}"
                    )
                    await self.session.apply_recovery_plan(recovery_plan)
            except Exception:
                # 恢复失败不阻断主流程（当前 run 会新建 operation）
                pass

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
                        tool_content = result.get("content", "")
                        if not tool_content or not str(tool_content).strip():
                            tool_content = result.get("error", "") or "工具执行完成（无返回内容）"
                        tool_entry = CrawlMessage(
                            id=uuid.uuid4().hex[:24],
                            parent_id=assistant_msg.id,
                            role="tool",
                            content=tool_content,
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

    @property
    def token_usage(self) -> TokenUsage:
        """当前 loop 累计的 token 用量（含缓存字段）。"""
        return self._token_usage

    def _record_usage(self, response: Any, llm: Any) -> None:
        """累计记录一次 LLM 响应的 token 用量（含缓存命中/未命中）。

        从 usage.py 提取（兼容 DeepSeek 顶层 prompt_cache_* 与 OpenAI cached_tokens），
        对 self._token_usage 做累加而不是覆盖。
        """
        try:
            from crawagent.llm.usage import _extract_usage_from_response
            u = _extract_usage_from_response(response)
            if not u:
                return
            self._token_usage.prompt_tokens += int(u.get("prompt_tokens", 0) or 0)
            self._token_usage.completion_tokens += int(u.get("completion_tokens", 0) or 0)
            self._token_usage.total_tokens += int(u.get("total_tokens", 0) or 0)
            self._token_usage.cache_hit_tokens += int(u.get("cache_hit_tokens", 0) or 0)
            self._token_usage.cache_miss_tokens += int(u.get("cache_miss_tokens", 0) or 0)
            self._token_usage.model = getattr(llm, "model_name", "") or self._token_usage.model or ""
        except Exception:
            pass

    # ---- 内部方法 ----

    async def _checkpoint(self) -> None:
        """创建 checkpoint：刷新写入 + 创建新 TurnSnapshot + 触发 Compaction"""
        self._phase = Phase.TURN

        # 全量读取（不截断）：长度由 compaction 阈值控制，避免截断切断缓存前缀
        entries = await self.session.get_entries(limit=100000)
        tool_names = list(self._tool_map.keys())

        # F6: Compaction 接入 — token 超阈值时压缩早期消息（复用实例保留压缩间隔状态）
        try:
            if self._compaction is None:
                from crawagent.harness.compaction import Compaction
                self._compaction = Compaction(
                    session=self.session,
                    hooks=self.hooks,
                    threshold=self._compaction_threshold,
                    keep_recent=10,
                )
            compaction = self._compaction
            if await compaction.should_compact(entries):
                from loguru import logger
                logger.info(
                    f"[Compaction] 触发压缩 (tokens≈{compaction.estimate_tokens(entries)})"
                )
                summary = await compaction.compact(entries, lane_name=self._lane_name)
                if summary is not None:
                    # 重新读取：summary + 最近消息（全量，不截断）
                    entries = await self.session.get_entries(limit=100000)
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
        # 获取上下文（默认 asc 正序：老→新，保证 tool 消息紧跟其 tool_calls；
        # 全量读取不截断，长度由 compaction 控制，避免截断切断缓存前缀）
        entries = await self.session.get_entries(limit=100000)

        # 修复 tool_calls / tool response 配对不完整问题：
        # 如果截取导致最后一条 assistant 有 tool_calls 但缺少对应的 tool response，
        # 去掉该 assistant 消息（否则 OpenAI API 会报 400 错误）
        entries = self._fix_tool_call_pairing(entries)

        # 系统提示词使用构造期缓存的稳定前缀（对齐 Reasonix cache-stable prefix）
        system_prompt = self._system_prompt

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
                msg_kwargs: Dict[str, Any] = {"content": e.content}
                if tcs:
                    msg_kwargs["tool_calls"] = tcs
                messages.append(AIMessage(**msg_kwargs))
            elif e.role == "tool":
                messages.append(ToolMessage(
                    content=e.content,
                    tool_call_id=e.tool_call_id or "",
                ))

        # transient turn-injection（对齐 Reasonix boot.go：mid-session changes never
        # touch the cache-stable prefix）：task_notes 作为追加的 system 消息放在列表
        # 末尾，而不是合并进首条 system，保证前缀字节不变。
        if self._task_notes:
            messages.append(SystemMessage(content=self._task_notes))

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

        # 回填 token 统计（累计 + 缓存字段，此前 TokenUsage 从未回填且是覆盖式）
        self._record_usage(response, llm)

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

        # ====== 兜底：LLM 无 tool_calls 但用户明确需要工具时，强制重试一次 ======
        if not tool_calls and self._needs_tools(entries):
            from loguru import logger
            logger.warning(
                f"[Loop] LLM 返回纯文本但无 tool_calls，检测到用户需要工具，强制重试..."
            )
            retry_content, retry_tool_calls = await self._force_tool_retry(
                messages, entries, content
            )
            if retry_tool_calls:
                content = retry_content
                tool_calls = retry_tool_calls
                logger.info(f"[Loop] 强制重试成功：{len(tool_calls)} 个 tool_calls")

        # ====== 补充描述：如果 LLM 调用工具但没有文字内容，自动添加描述 ======
        if tool_calls and (not content or not content.strip()):
            tool_names = [
                tc.get("function", {}).get("name", "unknown")
                if isinstance(tc, dict) and "function" in tc
                else tc.get("name", "unknown") if isinstance(tc, dict) else "unknown"
                for tc in tool_calls
            ]
            tool_display = "、".join(tool_names[:3])
            if len(tool_names) > 3:
                tool_display += f" 等 {len(tool_names)} 个"
            content = f"正在调用工具：{tool_display}..."

        # ====== 兜底：如果最终消息既无工具也无内容，添加默认文本 ======
        if not tool_calls and (not content or not content.strip()):
            content = "任务已完成。"

        # fire AFTER_RESPONSE hook
        resp_context: Dict[str, Any] = {"response": response, "lane": self._lane_name}
        resp_context = await self.hooks.fire(HookEvent.AFTER_RESPONSE, resp_context)

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

        # Security Hook 硬拦截：危险工具调用直接拒绝执行（优先于一切）
        if isinstance(tool_args, dict) and tool_args.get("_security_blocked"):
            return {
                "tool_call_id": tool_call_id,
                "content": "安全拦截：该工具调用已被 Security Hook 判定为危险操作（如写入敏感路径）",
                "error": True,
                "blocked": True,
            }

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
            if not result.get("content"):
                result["content"] = result.get("error") or f"工具 {tool_name} 执行完成"
            return result
        else:
            return {
                "tool_call_id": tool_call_id,
                "content": str(result) if result else f"工具 {tool_name} 执行完成",
            }

    def _fix_tool_call_pairing(self, entries: List[CrawlMessage]) -> List[CrawlMessage]:
        """修复 tool_calls / tool response 配对不完整问题。

        当消息列表被截取（limit 或 compaction）时，可能出现：
        - assistant 消息有 tool_calls=[A, B]，但后续缺少 A 或 B 的 tool response
        - OpenAI API 要求每个 tool_call_id 都必须有对应的 tool 消息

        修复策略：
        1. 扫描所有 assistant(tool_calls) 消息，收集 tool_call_id
        2. 扫描所有 tool 消息，收集已响应的 tool_call_id
        3. 对于缺少 tool response 的 assistant(tool_calls)，
           如果在消息末尾（正在执行中），去掉该 assistant 消息
           如果在中间，补充一个占位 tool response
        """
        if not entries:
            return entries

        # 收集所有已有的 tool_call_id（来自 tool 消息）
        answered_ids: set = set()
        for e in entries:
            if e.role == "tool" and e.tool_call_id:
                answered_ids.add(e.tool_call_id)

        # 检查每条 assistant(tool_calls) 是否都有对应的 tool response
        result: List[CrawlMessage] = []
        for i, e in enumerate(entries):
            if e.role == "assistant" and e.tool_calls:
                # 提取这条 assistant 消息中所有 tool_call_id
                tc_ids = []
                for tc in e.tool_calls:
                    if isinstance(tc, dict):
                        tc_id = tc.get("id", "")
                        if tc_id:
                            tc_ids.append(tc_id)

                if tc_ids:
                    # 检查后续消息中是否有对应的 tool response
                    missing_ids = [
                        tid for tid in tc_ids
                        if tid not in answered_ids
                    ]
                    if missing_ids:
                        # 是最后一条 assistant（tool 正在执行中，还没来得及生成 response）
                        is_last = (i == len(entries) - 1)
                        if is_last:
                            # 去掉这条 assistant（下一轮会重新调用）
                            from loguru import logger
                            logger.warning(
                                f"[Loop] 丢弃末尾未配对的 assistant(tool_calls): "
                                f"missing {len(missing_ids)} tool responses"
                            )
                            continue
                        else:
                            # 中间的缺失：补充占位 tool response
                            for mid in missing_ids:
                                placeholder = CrawlMessage(
                                    id=uuid.uuid4().hex[:24],
                                    parent_id=e.id,
                                    role="tool",
                                    content="(工具执行结果缺失，已被截断)",
                                    tool_call_id=mid,
                                )
                                result.append(placeholder)
            result.append(e)

        return result

    def _needs_tools(self, entries: List[CrawlMessage]) -> bool:
        """检查用户是否明显需要使用工具

        判断标准：
        1. 最新的 user 消息包含 URL、爬取/抓取/图片/搜索/分析等关键词
        2. 或者历史中已经有 tool_calls 但没有完成
        """
        # 找最新的 user 消息
        last_user_content = ""
        for e in reversed(entries):
            if e.role == "user":
                last_user_content = (e.content or "").lower()
                break

        if not last_user_content:
            return False

        # 关键词检测
        tool_keywords = [
            # 中文
            "http://", "https://", "www.", ".com", ".cn", ".net",
            "爬取", "抓取", "抓取", "提取", "采集", "扫描",
            "图片", "壁纸", "图像", "照片",
            "搜索", "查找", "检索",
            "分析", "监控", "检测",
            # 英文
            "crawl", "scrape", "extract", "fetch", "download",
            "image", "wallpaper", "picture", "photo",
            "search", "find", "lookup",
            "analyze", "monitor", "check",
        ]
        return any(kw in last_user_content for kw in tool_keywords)

    async def _force_tool_retry(
        self,
        messages: List[Any],
        entries: List[CrawlMessage],
        prev_content: str,
    ) -> tuple:
        """强制工具调用重试

        在系统提示中追加"必须使用工具"的指令，并重新调用 LLM。
        返回 (content, tool_calls) 元组。
        """
        from crawagent.llm.factory import get_llm
        from crawagent.harness.tools import to_langchain_tools
        from langchain_core.messages import SystemMessage

        # 构建强化系统提示：追加独立 SystemMessage 到末尾（不合并进首条 system，
        # 保证缓存稳定前缀字节不变，避免触发 OpenAI 兼容 API 400 与缓存失效）
        force_text = (
            "你必须使用工具来完成用户的任务。"
            "用户请求涉及网站操作（爬取、抓取、搜索等），"
            "你必须调用 supervisor 或 search 工具，而不是返回文字描述。"
            f"上一轮你返回了文字：'{prev_content[:100]}'，但没有调用任何工具。"
            "这一次，请直接调用工具。"
        )
        retry_messages = list(messages)
        retry_messages.append(SystemMessage(content=force_text))

        llm_tools = to_langchain_tools(self.tools)
        llm = get_llm(tools=llm_tools) if llm_tools else get_llm()

        try:
            response = await llm.ainvoke(retry_messages)
        except Exception as e:
            from loguru import logger
            logger.error(f"[Loop] 强制重试失败: {e}")
            return (prev_content, [])

        # 强制重试的 LLM 调用同样计入 token 累计
        self._record_usage(response, llm)

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

        return (content, tool_calls)

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
