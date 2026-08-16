"""CrawAgent Demo — 命令行交互入口

用法：
    python main.py              # 恢复上一次会话（首次运行则新建）
    python main.py <session_id> # 恢复/创建指定会话
    python main.py --new        # 强制新建会话

交互命令：
    quit / exit / q  退出
    clear            开启新会话（原会话保留，可用其 id 恢复）

会话记忆通过 LangGraph SqliteSaver 持久化到 data/sessions.db，
进程重启后按 session_id（即 thread_id）自动恢复上下文，无需向量库。
"""
import os
import sys
import socket
import time
import uuid
import sqlite3
import ctypes
from pathlib import Path
from urllib.parse import urlparse


def _enable_vt_escape() -> None:
    """Windows 终端启用 ANSI 转义序列（支持暗色、颜色等），不影响 macOS/Linux。"""
    if os.name == "nt":
        try:
            kernel32 = ctypes.windll.kernel32
            ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
            STD_OUTPUT_HANDLE = -11
            h = kernel32.GetStdHandle(STD_OUTPUT_HANDLE)
            mode = ctypes.c_ulong()
            if kernel32.GetConsoleMode(h, ctypes.byref(mode)):
                kernel32.SetConsoleMode(h, mode.value | ENABLE_VIRTUAL_TERMINAL_PROCESSING)
        except Exception:
            pass


_enable_vt_escape()

# ---- LangSmith 调试追踪：必须在导入 langchain 之前设置环境变量 ----
from dotenv import load_dotenv

load_dotenv()
if os.getenv("LANGSMITH_API_KEY") and not os.getenv("LANGSMITH_TRACING"):
    os.environ["LANGSMITH_TRACING"] = "true"


def _clear_dead_proxy_env():
    """清除指向不可达代理的环境变量。

    终端里可能残留代理工具写入的 HTTP_PROXY/HTTPS_PROXY/ALL_PROXY（及其小写形式），
    若代理软件已关闭，httpx/requests 仍会尝试走该代理 → WinError 10061 拒绝连接，
    拖垮 LLM、LangSmith、爬虫等所有 HTTP 请求。这里对每个已设置的代理做一次 TCP 探测，
    不可达则为本进程清除（不影响系统配置），可达则保留。
    """
    proxy_keys = ["HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"]
    for key in proxy_keys:
        val = os.environ.get(key)
        if not val:
            continue
        try:
            parsed = urlparse(val if "://" in val else "http://" + val)
            host = parsed.hostname
            if not host:
                continue
            port = parsed.port or (443 if "https" in val.lower() else 80)
            with socket.create_connection((host, port), timeout=1.5):
                pass  # 代理可达，保留
        except OSError:
            os.environ.pop(key, None)
            print(f"[proxy] {key}={val} 不可达，已为本进程清除（DeepSeek/LangSmith/爬虫均可直连）")


_clear_dead_proxy_env()

from loguru import logger
from langchain_core.messages import HumanMessage, AIMessage, ToolMessage
from langgraph.checkpoint.sqlite import SqliteSaver
from crawagent.config.settings import get_settings
from crawagent.graph.agent import get_agent
from crawagent.observability.metrics import SessionMetrics, usage_from_message

# 配置日志
logger.remove()
logger.add(sys.stderr, level="INFO", format="<green>{time:HH:mm:ss}</green> | <level>{level:<7}</level> | {message}")
logger.add("logs/crawagent.log", level="DEBUG", rotation="1 MB", retention="7 days", encoding="utf-8")


def _last_session_file() -> Path:
    """记录最近一次会话 id 的小文件，用于无参数启动时自动恢复"""
    return get_settings().project_root / "data" / ".last_session"


def _new_session_id() -> str:
    return uuid.uuid4().hex[:12]


def _load_last_session() -> str | None:
    p = _last_session_file()
    if p.exists():
        sid = p.read_text(encoding="utf-8").strip()
        if sid:
            return sid
    return None


def _save_last_session(session_id: str) -> None:
    p = _last_session_file()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(session_id, encoding="utf-8")


def _make_checkpointer() -> SqliteSaver:
    """创建基于 SQLite 的检查点存储，按 thread_id 持久化会话状态"""
    settings = get_settings()
    settings.sessions_db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(settings.sessions_db_path), check_same_thread=False)
    checkpointer = SqliteSaver(conn)
    checkpointer.setup()  # 建表（幂等）
    return checkpointer


def _resolve_session_id() -> str:
    """从命令行参数或最近会话记录中确定 session_id"""
    args = sys.argv[1:]
    if args:
        if args[0] in ("--new", "-n"):
            sid = _new_session_id()
            logger.info(f"新建会话: {sid}")
            return sid
        sid = args[0]
        logger.info(f"恢复会话: {sid}")
        return sid
    last = _load_last_session()
    if last:
        logger.info(f"恢复上次会话: {last}")
        return last
    sid = _new_session_id()
    logger.info(f"新建会话: {sid}")
    return sid


def main():
    """主循环：读取用户输入 → 调用 Agent → 输出结果"""
    logger.info("CrawAgent Demo 启动")
    if os.getenv("LANGSMITH_TRACING", "").lower() == "true":
        logger.info(f"LangSmith 追踪已启用 (project={os.getenv('LANGSMITH_PROJECT', 'default')})")
    else:
        logger.info("LangSmith 追踪未启用（在 .env 设置 LANGSMITH_API_KEY 即可开启）")
    logger.info("输入 URL 或任务描述，输入 quit 退出，输入 clear 开启新会话")

    # Agent、检查点存储、会话监控均只创建一次，跨轮次复用
    checkpointer = _make_checkpointer()
    agent = get_agent(checkpointer=checkpointer)
    metrics = SessionMetrics()  # UI 无关，终端/FastAPI 都从这里取数据
    session_id = _resolve_session_id()
    _save_last_session(session_id)
    logger.info(f"当前会话 ID: {session_id}（重启后输入 python main.py {session_id} 可恢复）")

    while True:
        try:
            user_input = input("\nspiderman >> ").strip()
        except (EOFError, KeyboardInterrupt):
            break

        if not user_input:
            continue
        if user_input.lower() in ("quit", "exit", "q"):
            break
        if user_input.lower() == "clear":
            old = session_id
            session_id = _new_session_id()
            _save_last_session(session_id)
            logger.info(f"已开启新会话: {session_id}（原会话 {old} 已保留，可用 python main.py {old} 恢复）")
            continue

        logger.info(f"发送任务: {user_input}")
        # thread_id 即 session_id，检查点存储据此加载历史状态并追加本轮消息
        config = {"configurable": {"thread_id": session_id}}

        try:
            metrics.turn_begin()
            # stream_mode="values" 每次产出完整状态快照，需先收集已存在消息 id，
            # 避免把检查点里的历史消息重复打印一遍
            shown_ids = set()
            state = agent.get_state(config)
            for m in state.values.get("messages", []):
                shown_ids.add(m.id)
            seen_llm_ids: set[str] = set()  # 已 on_llm_start 的 AIMessage.id（防止同一条多次触发 start）

            # 待配对的工具调用：tool_call_id → 起点时刻（AIMessage 里打印 [调用工具] 的时刻）
            pending_tool_starts: dict[str, float] = {}

            final_ai_content = ""
            # 只传入本轮新消息，检查点存储会自动并入历史上下文
            for event in agent.stream({"messages": [HumanMessage(content=user_input)]}, config=config, stream_mode="values"):
                for msg in event.get("messages", []):
                    if msg.id in shown_ids:
                        continue
                    shown_ids.add(msg.id)

                    if isinstance(msg, HumanMessage):
                        continue

                    elif isinstance(msg, AIMessage):
                        # 一条 AIMessage = 一次模型调用（tool_calls 请求 或 最终文本回复），按 msg.id 去重 start
                        # ponytail: CLI 用 values 流，快照在模型跑完后才到，故 LLM 耗时≈0、tok/s 无意义；
                        # token 数与步数仍准确。要真实计时需像 web/server.py 那样加 messages 流。
                        if msg.id not in seen_llm_ids:
                            seen_llm_ids.add(msg.id)
                            metrics.on_llm_start()

                        has_any_output = False
                        if msg.tool_calls:
                            has_any_output = True
                            for tc in msg.tool_calls:
                                args_preview = str(tc.get("args", {}))
                                if len(args_preview) > 120:
                                    args_preview = args_preview[:120] + "..."
                                print(f"\n  [调用工具] {tc['name']}({args_preview})")
                                # 工具真实执行是同步发生在 [调用工具] 打印之后、下一条 ToolMessage 出现之前
                                # 这里近似用打印时刻作为起点，配对 ToolMessage 出现时刻作为终点
                                pending_tool_starts[tc["id"]] = time.perf_counter()
                        if msg.content:
                            has_any_output = True
                            metrics.on_llm_first_token()  # 首次出现文本时记为 首 token 到达
                            print(f"\nAgent> {msg.content}")
                            final_ai_content = msg.content

                        # 同一条 AIMessage 只有拿到 token 用量才算一次 LLM 调用结束
                        token_usage = usage_from_message(msg)
                        if token_usage:
                            metrics.on_llm_end(token_usage)
                        elif has_any_output and not token_usage:
                            # 部分流式模型可能只在最后一个 event 的 AIMessage 上带 token_usage。
                            # 这种情况下我们先不 end：等后续 events 如果这条 AIMessage 的
                            # token_usage 被填充了，下次 stream 会被 shown_ids 过滤，不会走到这里。
                            # 作为兜底：轮次结束后补记一次（见下方）。
                            pass

                    elif isinstance(msg, ToolMessage):
                        preview = msg.content[:200] if msg.content else ""
                        if len(msg.content) > 200:
                            preview += "..."
                        print(f"\n  [工具结果] {preview}")
                        # 配对工具起点，有匹配则补一条完整的工具计时
                        if isinstance(msg.tool_call_id, str) and msg.tool_call_id in pending_tool_starts:
                            start = pending_tool_starts.pop(msg.tool_call_id)
                            tool_total = time.perf_counter() - start
                            metrics.tool_total_time += max(0.0, tool_total)
                            metrics.tool_call_count += 1
                        else:
                            # 没有配对上的起点（如 非流式 / 顺序打乱），就记 0 耗时，只增加计数
                            metrics.tool_call_count += 1

            metrics.turn_end()

            # 兜底：如果流式输出的最后一条 AIMessage 没带 token_usage，从 graph state 里读最新那条
            # LangGraph 完成的 state 里 AIMessage 通常已补全 response_metadata。
            if not seen_llm_ids and not final_ai_content:
                pass  # 没消息，跳过
            else:
                final_state = agent.get_state(config)
                for m in reversed(final_state.values.get("messages", [])):
                    if isinstance(m, AIMessage) and m.id in seen_llm_ids:
                        token_usage = usage_from_message(m)
                        if token_usage:
                            # 这里不能直接 on_llm_end（它会递增 llm_call_count，已经在第一次 AIMessage 时没 end → 无法精确配对）
                            # 最保险的做法：只补 token 统计，不重复计入调用数和耗时（耗时由 on_llm_end 时记录）
                            metrics.input_tokens += int(token_usage.get("prompt_tokens") or 0)
                            metrics.output_tokens += int(token_usage.get("completion_tokens") or 0)
                            metrics.cache_hit_tokens += int(token_usage.get("prompt_cache_hit_tokens") or 0)
                            metrics.cache_miss_tokens += int(token_usage.get("prompt_cache_miss_tokens") or 0)
                        break

            if not final_ai_content:
                logger.warning("Agent 未返回消息")

            # 终端状态栏：打印一行和你截图里同款的汇总信息
            # （样式示例：1 轮·2 步 | LLM 10.3s · 工具 1.2s | 首 token 平均 840ms · 58 tok/s | 缓存命中 32% | 输入 920 tok · 输出 140 tok）
            print("\n\033[2m" + metrics.status_line() + "\033[0m")

        except Exception as e:
            logger.error(f"执行失败: {e}")
            # 注意：使用检查点存储后，本轮 HumanMessage 可能已被持久化；
            # 若执行失败，下一轮 Agent 仍可正常工作，如需干净状态可输入 clear 开启新会话

    logger.info("CrawAgent 退出")


if __name__ == "__main__":
    main()
