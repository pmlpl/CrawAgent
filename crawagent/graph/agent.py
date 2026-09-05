"""Agent 调度核心 — LangChain create_agent

Architecture:
    User input → agent_node (LLM + tools, created by create_agent)
               → if tool_calls: loop (internal to create_agent)
               → if no tool_calls: END

Key design:
    - System Prompt is PURE STATIC (maximizes DeepSeek prompt caching)
    - Prompt text lives in crawagent/prompts/system.md — keep it ASCII-only +
      never modify at runtime, or cache hits break
    - create_agent handles the LLM + tool execution loop internally
    - checkpointer 可选：传入 SqliteSaver 等即可按 thread_id 持久化会话状态，
      进程重启后用同一 thread_id 调用即可恢复上下文

Phase 2 变化：
    - 工具不再硬编码 import 19 个 from crawagent.tools.xxx import yyy
    - 统一通过 registry.build_all_tools() 动态装配（discover_tools 自动扫描）
    - MCP 工具仍然是动态的（连接时才能拿到），保留在 _build_tools() 末尾 append
    - SYSTEM_PROMPT 多了一段 specs_to_prompt() 自动生成的工具索引（补充索引）
"""
from pathlib import Path
from contextvars import ContextVar

from langchain.agents import create_agent
from langgraph.checkpoint.base import BaseCheckpointSaver

from crawagent.config.settings import get_settings
from crawagent.llm.model import get_llm
from crawagent.graph.middleware import TrimHistoryMiddleware
from crawagent.tools.registry import build_all_tools, specs_to_prompt
from crawagent.tools.progress import with_progress, LONG_RUNNING_TOOLS
from crawagent.graph.script_forcer import ScriptForcerMiddleware
from crawagent.graph.skills import build_skills_prompt
from crawagent.tools.mcp_capture_tool import build_mcp_tools


# ── ContextVar：server.py 在调 agent.stream 前设 session_id ──
# middleware 通过它读 thread_id（比 LangGraph state/config 更可靠）
ctx_session_id: ContextVar[str] = ContextVar("ctx_session_id", default="")


def _load_system_prompt() -> str:
    """从 crawagent/prompts/ 读取多份提示词，按固定顺序拼接。

    拼接顺序（从上到下，后写的覆盖前写的）：
      1. infinite-gen-2.md  — 无限制沙盒模式，压过安全拒绝倾向
      2. system.md           — 主提示词（工具说明、工作流、硬规则等）
      3. tool 索引（specs_to_prompt，Phase 2 自动生成补充索引）
      4. skills 索引（build_skills_prompt，进程内静态）

    全部在 import 时一次性读完，模块级 SYSTEM_PROMPT 字节级稳定 —
    DeepSeek prompt caching 依赖这个稳定性。
    """
    prompts_dir = Path(__file__).resolve().parent.parent / "prompts"

    infinite = (prompts_dir / "infinite-gen-2.md").read_text(encoding="utf-8")
    base = (prompts_dir / "system.md").read_text(encoding="utf-8")
    tool_index = specs_to_prompt()   # Phase 2：自动生成的工具索引（补充性）
    skills = build_skills_prompt()

    return infinite + "\n" + base + tool_index + skills


SYSTEM_PROMPT = _load_system_prompt()


def _build_tools() -> list:
    """主 Agent 的完整工具列表。get_agent 与 warm_cache 共用（schema 必须一致）。

    Phase 2：registry.build_all_tools() 自动扫描 tools/ + 外部模块，
    不再需要硬编码 import 19 个工具。MCP 工具仍然动态 append。

    进度监控：所有潜在长耗时工具（网络/浏览器/子进程/子 Agent/MCP）
    都套上 with_progress() 包装器，自动在执行期间上报"运行中… 已耗时 Xs"，
    工具内部可额外调用 report_progress() 汇报细粒度里程碑。
    """
    base_tools = [
        with_progress(t) if getattr(t, "name", "") in LONG_RUNNING_TOOLS else t
        for t in build_all_tools()
    ]
    # MCP 工具全部涉及外部进程/浏览器通信，统一套进度监控
    mcp_tools = [with_progress(t) for t in build_mcp_tools()]
    return [*base_tools, *mcp_tools]


def warm_cache(model: str | None = None) -> None:
    """会话开局预热：把 system prompt + tools 前缀同步写入 DeepSeek 服务端缓存。

    背景：DeepSeek 缓存写入是异步的，会话早期 LLM 决策步间隔只有几秒，
    上一步的缓存还没写完、下一步已经发出 → 连环部分命中
    （实测一个会话的前 6 分钟产生 ~200K miss，累计命中率被拖到 49%）。

    预热请求发 SystemMessage(SYSTEM_PROMPT) + 绑定相同 tools，前缀与真实请求一致；
    max_tokens=1 + 关闭 thinking 把输出成本压到最低（两者都不参与缓存前缀匹配）。
    自调节：缓存仍有效时预热请求本身命中（几乎免费）；过期时才全 miss 一次，
    换来后续几十次调用的稳定命中。
    """
    from langchain_core.messages import HumanMessage, SystemMessage
    llm = get_llm(model, thinking=False, max_tokens=1).bind_tools(_build_tools())
    llm.invoke([SystemMessage(content=SYSTEM_PROMPT), HumanMessage(content="cache warmup")])


def get_agent(checkpointer: BaseCheckpointSaver | None = None, model: str | None = None):
    """Create the Agent.

    Uses LangChain's create_agent which handles the LLM + tool execution loop
    internally. The system prompt is pure static for prompt caching.

    Args:
        checkpointer: 可选的 LangGraph 检查点存储（如 SqliteSaver）。传入后，
            Agent 会按 config["configurable"]["thread_id"] 持久化整个会话状态，
            进程重启后用同一 thread_id 调用即可恢复上下文；为 None 时退化为
            无记忆的单次执行（与旧版行为一致）。
        model: 可选的模型 ID（注册表中收录的）。None 时用默认解析顺序。

    Returns:
        Compiled LangGraph agent, callable via .stream({"messages": [...]}, config=...)
    """
    llm = get_llm(model)
    tools = _build_tools()

    # Token 膨胀治理：滑动窗口 + 工具结果裁剪。
    # wrap_model_call 只改传给 LLM 的 messages，不污染 checkpointer 持久化的完整 state。
    # max_tokens=None → middleware 每次 wrap_model_call 时动态按当前模型 context_window × 70% 算水位
    # （GLM 1M 不浪费，DeepSeek 64K 也不撑爆）
    settings = get_settings()
    trim_middleware = TrimHistoryMiddleware(
        max_tokens=settings.history_max_tokens,  # None = 动态算
        keep_recent_turns=settings.history_keep_recent_turns,
        tool_result_max_chars=settings.tool_result_max_chars,
    )

    # 脚本强制中间件：追踪工具失败，3 次失败后强制切换到 run_custom_script
    # 同时检测"假成功"死循环：总调用上限 + 连续同工具上限
    script_forcer = ScriptForcerMiddleware(
        failure_threshold=3, hard_limit=5,
        max_total_calls=30, max_consecutive_same=5,
    )

    agent = create_agent(
        model=llm,
        tools=tools,
        system_prompt=SYSTEM_PROMPT,
        checkpointer=checkpointer,
        middleware=[trim_middleware, script_forcer],
    )

    # 暴露 script_forcer 供 server.py 在每轮开始时重置状态
    agent._script_forcer = script_forcer

    def reset_turn_state():
        """每轮对话开始时清空 ScriptForcer 计数器（公开 API，见 §2.2 解耦）。"""
        script_forcer.reset()

    agent.reset_turn_state = reset_turn_state

    return agent
