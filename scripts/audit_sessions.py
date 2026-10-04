"""042 会话行为审计 — 离线只读扫描 checkpointer sqlite 的真实会话消息流。

用法：
  uv run python scripts/audit_sessions.py                 # 最近 7 天（默认）
  uv run python scripts/audit_sessions.py --all           # 全量
  uv run python scripts/audit_sessions.py --days 30       # 自定义窗口
  uv run python scripts/audit_sessions.py --sessions id1,id2
  uv run python scripts/audit_sessions.py --out PATH      # 报告落点（默认 data/audits/audit.md）

机制（变更 042）：
- 数据源：settings.sessions_db_path 的 SqliteSaver 检查点（与 build_checkpointer
  同一构造、URI 只读打开）；EventLog 是内存态逐轮事件，重启即丢，不能作审计数据源。
- 轮次切分：以 HumanMessage 为界，一轮 = 用户指令 + 其后连续的
  AIMessage(tool_calls) / ToolMessage 序列（与 routers/sessions/crud.py 同一读取口径）。
- 五条确定性规则（不用 LLM 当裁判），判据与 scripts/eval_cases.py 同一契约语义，
  锚行为流不锚问句文本（041 实测模型会意译问句）：
    R1 SCRIPT-FIRST-NO-PROFILE  首次 run_custom_script 前既无 list_site_profiles 也无内置工具失败
    R2 ARCHIVE-ASK-MISSING      列表/抓取类工具成功返回的轮内既无 ask_user 也无 save_record
    R3 SAME-ARGS-RETRY          同工具同参数重复调用且前一次结果为失败
    R4 MCP-ADD-NO-DUP-CHECK     add_mcp_server 前本会话没有 list_mcp_servers
    R5 COOKIE-NO-ASK            微信读书工具报 Cookie 未配置后未经 ask_user 又直调同类工具
- low 置信度命中单独归「待判读」节，防误报刷屏变成狼来了。
- 纯旁路：绝不写检查点、不影响运行时行为；报告先在内存算完再一次性落盘（不留半份报告）。
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage  # noqa: E402
from langgraph.checkpoint.sqlite import SqliteSaver  # noqa: E402

# ---------------------------------------------------------------------------
# 数据模型
# ---------------------------------------------------------------------------


@dataclass
class ToolCall:
    """一次工具调用 + 配对到的 ToolMessage 结果（未配对则 result 为空串）。"""

    name: str
    args: dict
    call_id: str
    turn: int
    result: str = ""


@dataclass
class Turn:
    index: int  # 1-based；0 表示首条用户消息之前的孤儿消息
    user_text: str
    calls: list[ToolCall] = field(default_factory=list)


@dataclass
class SessionRec:
    thread_id: str
    title: str
    ts: str  # 最新 checkpoint 的 ISO 时间戳（原样保留）
    turns: list[Turn] = field(default_factory=list)
    parse_error_turns: int = 0

    @property
    def calls(self) -> list[ToolCall]:
        return [c for t in self.turns for c in t.calls]


@dataclass
class Finding:
    rule: str
    session_id: str
    title: str
    turn: int
    confidence: str  # high | low
    evidence: str
    suggestion: str


# ---------------------------------------------------------------------------
# 基础判定工具
# ---------------------------------------------------------------------------

# 已知工具失败输出前缀（来自各工具源码的错误返回约定；只看开头防止
# 抓回来的正文里恰好出现「失败」字样造成误判）
_FAIL_PREFIXES = (
    "[EXIT CODE",      # run_custom_script 非零退出
    "[TIMEOUT]",       # run_custom_script 超时
    "[ERROR]",         # run_custom_script/markitdown_convert 等通用异常
    "[SPA_SHELL_DETECTED]",      # crawl_webpage SPA 空壳（契约定义为失败）
    "[WEREAD_COOKIE_NOT_SET]",   # 微信读书 Cookie 未配置
    "ERROR:",          # crawl_webpage 请求异常
    "ERR:",            # android_reverse_tool 系列（adb/frida 未安装等）
    "Browse failed",   # browse_and_crawl 渲染/导航失败
    "Traceback",
    "List extraction failed",    # extract_list / extract_list_paged
    "抓取失败",
)

# 认证/风控墙开头特征：内容提取被登录或验证码拦截 = 无可用内容（AUTH/PAYWALL
# 阶梯的管辖，不算「抓取完成」）。只看开头 120 字符，避免误伤正文里顺带提到
# 登录的页面。「验证码中间页」来自 browse_and_crawl 撞抖音风控页的真实输出
# （2026-10-04 真机 R2 误报复核：被当抓取成功收尾）。
_AUTH_WALL_MARKERS = ("需要登录", "请先登录", "login required", "please sign in", "验证码中间页")


def is_failure(result: str) -> bool:
    """ToolMessage 内容是否为失败特征（空结果也按失败计）。"""
    if not result:
        return True
    head = result[:200]
    if head.startswith(_FAIL_PREFIXES):
        return True
    if head[:60].lower().startswith(("error", "failed", "exception")):
        return True
    if any(m in result[:120].lower() for m in _AUTH_WALL_MARKERS):
        return True
    stripped = result.lstrip()
    if stripped[:1] in ("{", "["):
        try:
            data = json.loads(stripped)
        except Exception:
            data = None
        if isinstance(data, dict) and "error" in data:
            return True
    return False


def is_cookie_error(result: str) -> bool:
    """ToolMessage 是否为「Cookie 未配置」类错误（R5 触发器）。"""
    if not result:
        return False
    if "[WEREAD_COOKIE_NOT_SET]" in result[:200]:
        return True
    low = result[:300].lower()
    return "cookie" in low and ("未配置" in low or "not set" in low or "not configured" in low)


_URL_RE = re.compile(r"https?://[^\s\"'()]+")

# 参与域名比对的工具参数键（提示性，仅用于 R1 降置信度）
_URL_ARG_KEYS = ("url", "base_url", "origin")


def _host_of(url: str) -> str:
    try:
        host = urlparse(url.strip().strip("\"'")).netloc.lower()
    except Exception:
        return ""
    return host[4:] if host.startswith("www.") else host


def _site_key(host: str) -> str:
    """取「注册域」级 key（末两段），消 www/子域差异；仅提示性用途。"""
    parts = [p for p in host.split(".") if p]
    if len(parts) >= 2:
        return ".".join(parts[-2:])
    return host


def _domains_in_args(args: dict) -> set[str]:
    out: set[str] = set()
    for key in _URL_ARG_KEYS:
        val = args.get(key)
        if isinstance(val, str):
            for u in _URL_RE.findall(val):
                h = _host_of(u)
                if h:
                    out.add(_site_key(h))
    return out


def _domains_in_text(text: str) -> set[str]:
    out: set[str] = set()
    for u in _URL_RE.findall(text or ""):
        h = _host_of(u)
        if h:
            out.add(_site_key(h))
    return out


# ---------------------------------------------------------------------------
# 消息流解析
# ---------------------------------------------------------------------------


def _content_text(m: Any) -> str:
    """AIMessage/HumanMessage/ToolMessage 的 content 统一拍平成 str。"""
    content = getattr(m, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, (list, tuple)):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                parts.append(str(block.get("text") or block.get("content") or ""))
            else:
                parts.append(str(block))
        return "\n".join(parts)
    return str(content) if content else ""


def parse_messages(messages: Any) -> SessionRec:
    """把一个会话的消息列表切轮并配对工具调用/结果。

    消息结构异常（非 list、未知类型、孤儿 ToolMessage）：跳过并计
    parse_error_turns，不中断。
    """
    if not isinstance(messages, (list, tuple)):
        return SessionRec(thread_id="", title="", ts="", turns=[], parse_error_turns=1)

    rec = SessionRec(thread_id="", title="", ts="")
    calls_by_id: dict[str, ToolCall] = {}
    bad_turns: set[int] = set()

    for m in messages:
        if isinstance(m, HumanMessage):
            rec.turns.append(Turn(index=len(rec.turns) + 1, user_text=_content_text(m)))
            continue
        if isinstance(m, (AIMessage, ToolMessage)):
            if not rec.turns:
                # 首条消息不是用户消息（历史遗留/系统注入）：归入第 1 轮空轮
                rec.turns.append(Turn(index=1, user_text=""))
        else:
            # 未知类型（含 SystemMessage 等噪声）：按异常计，跳过
            bad_turns.add(len(rec.turns))
            continue
        turn = rec.turns[-1]
        if isinstance(m, AIMessage):
            for tc in getattr(m, "tool_calls", None) or []:
                call = ToolCall(
                    name=str(tc.get("name") or ""),
                    args=tc.get("args") or {},
                    call_id=str(tc.get("id") or ""),
                    turn=turn.index,
                )
                turn.calls.append(call)
                if call.call_id:
                    calls_by_id[call.call_id] = call
        else:  # ToolMessage
            call = calls_by_id.get(getattr(m, "tool_call_id", ""))
            if call is not None:
                call.result = _content_text(m)
            else:
                bad_turns.add(turn.index)
    rec.parse_error_turns = len(bad_turns)
    return rec


# ---------------------------------------------------------------------------
# 五条规则
# ---------------------------------------------------------------------------

SCRIPT_TOOL = "run_custom_script"
PROFILE_TOOL = "list_site_profiles"

# R2 触发集：列表/抓取类工具（成功返回才算）。run_custom_script 不在 v1 触发集
# （脚本产物是否「抓取完成」无法确定性判定，宁缺毋滥）；
# download_* / extract_social_media 是用户自取的媒体/元数据，不是知识库候选，
# 首扫实测算触发器会刷误报（校准记录见规格 §八）。
_CRAWL_TOOLS = {
    "crawl_webpage",
    "extract_content",
    "extract_list",
    "extract_list_paged",
    "browse_and_crawl",
    "crawl4ai_deep_crawl",
    "browser_use_navigate",
    "extract_wallpaper_list",
}

# ARCHIVE-ASK 契约：extract_list_paged 5+ 条合并结果才算「完成的多页抓取」
_LIST_ITEMS_RE = re.compile(r"List \((\d+) items")

# 用户在本轮指令里明说不用存档 → 该轮免问（合法例外）
_USER_DECLINE_MARKERS = ("不用存", "不要存", "别存", "不必存", "不用存档", "不再存档")

# ask_user 答复含「本会话不再询问」→ 其后各轮免问（ARCHIVE-ASK 契约选项之一）
_NO_MORE_ASK_MARKER = "不再询问"

_SUGGESTIONS = {
    "R1": "核对是否有跨会话档案/失败依据；无则契约要求先 list_site_profiles 查档案再动手",
    "R2": "抓取完成当轮必须 ask_user 问存档（用户已拒存或答「本会话不再询问」则免）",
    "R3": "同参数原样重试是契约禁止项：ERR 后按 L1 换 UA、L2 换代理、L3 换路线",
    "R4": "add_mcp_server 前必须 list_mcp_servers 防重名",
    "R5": "Cookie 未配置报错后必须 ask_user 索取，禁止再直调微信读书工具",
}


def _finding(rule: str, session: SessionRec, turn: int, confidence: str, evidence: str) -> Finding:
    return Finding(
        rule=rule,
        session_id=session.thread_id,
        title=session.title,
        turn=turn,
        confidence=confidence,
        evidence=evidence,
        suggestion=_SUGGESTIONS[rule],
    )


# 用户明说要写脚本 → 脚本直跑是遵从用户指令，不算脚本先行违规
_USER_SCRIPT_REQUEST_MARKERS = (
    "写一个脚本", "写个脚本", "写一段脚本", "写脚本", "脚本试试", "放一个脚本",
)


def rule_script_first_no_profile(session: SessionRec) -> list[Finding]:
    """R1：首次 run_custom_script 前既无档案查询也无内置工具失败。

    合法例外：list_site_profiles 在前（契约允许脚本直取）；失败在前
    （「first sign of failure」合规，失败与脚本抽不出可比域名时也视为同任务）；
    用户本轮明说要写脚本（遵从用户指令）。
    失败域与脚本域都可抽取且互不相交 → 仍命中但降 low。
    """
    calls = session.calls
    first_script = next((c for c in calls if c.name == SCRIPT_TOOL), None)
    if first_script is None:
        return []
    prior = calls[: calls.index(first_script)]
    if any(c.name == PROFILE_TOOL for c in prior):
        return []
    failures = [c for c in prior if c.result is not None and is_failure(c.result)]
    if failures:
        script_domains = _domains_in_text(str(first_script.args.get("code", "")))
        fail_domains: set[str] = set()
        for c in failures:
            fail_domains |= _domains_in_args(c.args)
            fail_domains |= _domains_in_text(c.result[:500])
        if not script_domains or not fail_domains:
            return []  # 任一侧抽不出域名 → 视为同一任务的失败在先，合规
        if script_domains & fail_domains:
            return []  # 同域失败在先 → 合规
        evidence = (
            f"首次 {SCRIPT_TOOL} 前 0 次档案查询；有 {len(failures)} 次失败但目标域疑似不同"
            f"（失败域: {sorted(fail_domains)}；脚本域: {sorted(script_domains)}）"
        )
        return [_finding("R1", session, first_script.turn, "low", evidence)]
    user_text = _turn_user_text(session, first_script.turn)
    if any(m in user_text for m in _USER_SCRIPT_REQUEST_MARKERS):
        return []  # 用户明说要写脚本
    evidence = f"首次 {SCRIPT_TOOL} 前 {PROFILE_TOOL} 0 次、内置工具失败 0 次"
    return [_finding("R1", session, first_script.turn, "high", evidence)]


def _turn_user_text(session: SessionRec, turn_index: int) -> str:
    for t in session.turns:
        if t.index == turn_index:
            return t.user_text or ""
    return ""


def _crawl_success(call: ToolCall) -> bool:
    """R2 的「成功返回」判定：非失败且（列表类）条数达契约门槛。"""
    if is_failure(call.result):
        return False
    if call.name in ("extract_list", "extract_list_paged"):
        m = _LIST_ITEMS_RE.search(call.result[:200])
        return bool(m) and int(m.group(1)) >= 5
    return True


def rule_archive_ask_missing(session: SessionRec) -> list[Finding]:
    """R2：列表/抓取类工具成功返回且当轮以它收尾，轮内既无 ask_user 也无 save_record。

    锚「有没有 ask_user」而非锚问句文本（041 实测 glm-5.2 意译问句概率约一半）。
    「以抓取收尾」：契约禁的是「抓完就结束回合不问」（system.md 多页清单条款），
    抓完还继续干别的活（脚本加工/换路）说明任务未完，不算收尾——首扫实测
    该收紧把「任务被打断/中途继续」的误报清干净。
    合法例外：用户本轮明说不用存；此前轮 ask_user 答复「本会话不再询问」。
    """
    findings: list[Finding] = []
    no_more_ask = False
    for turn in session.turns:
        for c in turn.calls:
            if c.name == "ask_user" and _NO_MORE_ASK_MARKER in (c.result or ""):
                no_more_ask = True
        user_declined = any(m in (turn.user_text or "") for m in _USER_DECLINE_MARKERS)
        if user_declined or no_more_ask or not turn.calls:
            continue
        last = turn.calls[-1]
        if last.name not in _CRAWL_TOOLS or not _crawl_success(last):
            continue
        has_ask = any(c.name == "ask_user" for c in turn.calls)
        has_save = any(c.name in ("save_record", "save_site_profile") for c in turn.calls)
        if has_ask or has_save:
            continue
        evidence = f"{last.name} 成功返回且当轮以其收尾，但轮内无 ask_user 也无 save_record"
        findings.append(_finding("R2", session, turn.index, "high", evidence))
    return findings


def rule_same_args_retry(session: SessionRec) -> list[Finding]:
    """R3：同工具同名 + 同参数（args JSON 规范化后相等）重复调用且前次失败。"""
    findings: list[Finding] = []
    seen: dict[str, ToolCall] = {}
    counts: dict[str, int] = {}
    for c in session.calls:
        try:
            key = c.name + "::" + json.dumps(c.args, sort_keys=True, ensure_ascii=False)
        except Exception:
            key = c.name + "::" + repr(c.args)
        counts[key] = counts.get(key, 0) + 1
        prev = seen.get(key)
        if prev is not None and is_failure(prev.result):
            preview = (prev.result or "")[:60].replace("\n", " ")
            evidence = f"{c.name} 同参数第 {counts[key]} 次调用，前次结果失败（{preview}…）"
            findings.append(_finding("R3", session, c.turn, "high", evidence))
        seen[key] = c
    return findings


def rule_mcp_add_no_dup_check(session: SessionRec) -> list[Finding]:
    """R4：add_mcp_server 调用时本会话此前没有 list_mcp_servers（防重名）。"""
    findings: list[Finding] = []
    seen_list = False
    for c in session.calls:
        if c.name == "list_mcp_servers":
            seen_list = True
        elif c.name == "add_mcp_server" and not seen_list:
            evidence = f"add_mcp_server 前无 list_mcp_servers 调用（第 {c.turn} 轮）"
            findings.append(_finding("R4", session, c.turn, "high", evidence))
    return findings


_WEREAD_TOOLS = {"list_weread_chapters", "get_weread_chapter"}


def rule_cookie_no_ask(session: SessionRec) -> list[Finding]:
    """R5：微信读书工具报 Cookie 未配置后，未经 ask_user 索取又直调同类工具。

    一律 low 置信度（进「待判读」节）。
    """
    findings: list[Finding] = []
    calls = session.calls
    for i, c in enumerate(calls):
        if c.name not in _WEREAD_TOOLS or not is_cookie_error(c.result):
            continue
        asked = False
        for c2 in calls[i + 1:]:
            if c2.name == "ask_user":
                asked = True
                break
            if c2.name in _WEREAD_TOOLS:
                if not asked:
                    evidence = (
                        f"{c.name} 报 Cookie 未配置后，未经 ask_user 又调 {c2.name}（第 {c2.turn} 轮）"
                    )
                    findings.append(_finding("R5", session, c2.turn, "low", evidence))
                break
    return findings


RULES: dict[str, Callable[[SessionRec], list[Finding]]] = {
    "R1": rule_script_first_no_profile,
    "R2": rule_archive_ask_missing,
    "R3": rule_same_args_retry,
    "R4": rule_mcp_add_no_dup_check,
    "R5": rule_cookie_no_ask,
}

RULE_LABELS = {
    "R1": "脚本先行无档案",
    "R2": "抓完不问档",
    "R3": "同参数重试",
    "R4": "MCP 加前不查重",
    "R5": "Cookie 不索取",
}


def audit_session(session: SessionRec) -> list[Finding]:
    findings: list[Finding] = []
    for fn in RULES.values():
        findings.extend(fn(session))
    return findings


# ---------------------------------------------------------------------------
# 数据装载（只读）
# ---------------------------------------------------------------------------


def open_readonly(db_path: Path) -> sqlite3.Connection:
    """URI 只读打开检查点库：绝不写文件（含 -wal/-shm 收尾）。"""
    uri = db_path.resolve().as_uri() + "?mode=ro"
    return sqlite3.connect(uri, uri=True, check_same_thread=False)


def list_thread_ids(conn: sqlite3.Connection) -> list[str]:
    try:
        rows = conn.execute(
            "SELECT thread_id, MAX(rowid) AS latest FROM checkpoints "
            "GROUP BY thread_id ORDER BY latest DESC"
        ).fetchall()
    except sqlite3.OperationalError:
        return []  # checkpoints 表不存在（空库）
    return [str(r[0]) for r in rows]


def load_sessions(
    settings: Any,
    *,
    all_scopes: bool = False,
    days: int = 7,
    only_ids: set[str] | None = None,
    title_fn: Callable[[str, Any], str] | None = None,
) -> tuple[list[SessionRec], int]:
    """拉取范围内会话并解析成 SessionRec 列表。

    Returns:
        (会话列表, 解析失败轮总数, 纯对话跳过数)。纯对话（无任何工具调用）会话不返回。
    """
    if title_fn is None:
        def title_fn(tid: str, _settings: Any) -> str:
            from crawagent.storage.meta_store import get_session_title

            return get_session_title(tid, _settings)

    db_path = Path(settings.sessions_db_path)
    if not db_path.exists():
        return [], 0, 0
    conn = open_readonly(db_path)
    saver = SqliteSaver(conn)  # 只读用途，不调 setup()（避免只读连接上建表）
    try:
        if only_ids:
            thread_ids = list(only_ids)
        else:
            thread_ids = list_thread_ids(conn)

        cutoff = None
        if not all_scopes:
            cutoff = datetime.now(timezone.utc) - timedelta(days=days)

        sessions: list[SessionRec] = []
        parse_errors = 0
        dialog_only = 0
        for tid in thread_ids:
            try:
                tup = saver.get_tuple({"configurable": {"thread_id": tid}})
            except Exception:
                parse_errors += 1
                continue
            if tup is None:
                continue
            ts = str((tup.checkpoint or {}).get("ts") or "")
            if cutoff is not None and not _within(ts, cutoff):
                continue
            msgs = ((tup.checkpoint or {}).get("channel_values") or {}).get("messages") or ()
            rec = parse_messages(msgs)
            rec.thread_id = tid
            rec.ts = ts
            try:
                rec.title = title_fn(tid, settings) or ""
            except Exception:
                rec.title = ""
            if not rec.calls:
                dialog_only += 1  # 纯对话 / 空会话：跳过，不计违规
                continue
            parse_errors += rec.parse_error_turns
            sessions.append(rec)
        return sessions, parse_errors, dialog_only
    finally:
        conn.close()


def _within(ts: str, cutoff: datetime) -> bool:
    if not ts:
        return True  # 无时间戳的旧数据按范围内处理（宁可多扫）
    try:
        dt = datetime.fromisoformat(ts)
    except ValueError:
        return True
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt >= cutoff


# ---------------------------------------------------------------------------
# 报告
# ---------------------------------------------------------------------------


def build_report(
    findings: list[Finding],
    *,
    scope_label: str,
    sessions_scanned: int,
    turns_scanned: int,
    dialog_only_skipped: int,
    parse_error_turns: int,
) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    high = [f for f in findings if f.confidence == "high"]
    low = [f for f in findings if f.confidence != "high"]

    lines: list[str] = [f"## {now}（范围：{scope_label}）", ""]

    def _fmt(f: Finding) -> str:
        title = (f.title or "（无标题）").replace('"', "'")
        sid8 = f.session_id[:8]
        return f'- [{f.rule}] 会话"{title}"({sid8}) 第 {f.turn} 轮 | 证据: {f.evidence} | 建议: {f.suggestion}'

    lines.append("### 违规（high）")
    lines.append("")
    if high:
        lines.extend(_fmt(f) for f in high)
    else:
        lines.append("（无）")
    lines.append("")
    lines.append("### 待判读（low）")
    lines.append("")
    if low:
        lines.extend(_fmt(f) for f in low)
    else:
        lines.append("（无）")
    lines.append("")

    counts = {r: sum(1 for f in findings if f.rule == r) for r in RULES}
    lines.append("### 统计")
    lines.append("")
    lines.append(
        f"- 扫描会话 {sessions_scanned} 个（纯对话跳过 {dialog_only_skipped} 个）"
        f" | 轮次 {turns_scanned} 个 | 解析失败轮 {parse_error_turns} 个"
    )
    lines.append(
        "- 命中数：" + " ".join(f"{r}={counts[r]}" for r in RULES)
        + f"（high {len(high)} / low {len(low)}）"
    )
    lines.append("")
    return "\n".join(lines)


def summarize_stdout(
    findings: list[Finding], sessions_scanned: int, turns_scanned: int, out_path: Path
) -> str:
    counts = {r: sum(1 for f in findings if f.rule == r) for r in RULES}
    lines = [
        f"扫描会话 {sessions_scanned} 个 | 轮次 {turns_scanned} 个 | 命中 "
        + " ".join(f"{r}={counts[r]}" for r in RULES),
        f"报告: {out_path}",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(
    argv: list[str] | None = None,
    settings: Any = None,
    title_fn: Callable[[str, Any], str] | None = None,
) -> int:
    ap = argparse.ArgumentParser(description="CrawAgent 会话行为审计（变更 042）")
    ap.add_argument("--all", action="store_true", help="扫描全部历史会话（默认最近 7 天）")
    ap.add_argument("--days", type=int, default=7, help="时间窗口天数（默认 7）")
    ap.add_argument("--sessions", type=str, default="", help="只扫指定会话 id（逗号分隔）")
    ap.add_argument("--out", type=str, default="", help="报告路径（默认 data/audits/audit.md）")
    args = ap.parse_args(argv)

    if settings is None:
        from crawagent.config.settings import get_settings

        settings = get_settings()

    backend = getattr(settings, "checkpoint_backend", "sqlite") or "sqlite"
    if backend != "sqlite":
        print(f"checkpoint_backend={backend} 不是 sqlite：审计只支持本机 sqlite 检查点库，退出。", file=sys.stderr)
        return 2

    only_ids = {s.strip() for s in args.sessions.split(",") if s.strip()} or None
    if only_ids:
        # 指定 --sessions 时无视时间窗（用户点名要看）
        sessions, parse_errors, dialog_only = load_sessions(
            settings, all_scopes=True, only_ids=only_ids, title_fn=title_fn
        )
        scope_label = f"指定 {len(only_ids)} 个会话"
    else:
        sessions, parse_errors, dialog_only = load_sessions(
            settings, all_scopes=args.all, days=max(args.days, 1), title_fn=title_fn
        )
        scope_label = "all" if args.all else f"最近 {args.days} 天"
    findings: list[Finding] = []
    turns_scanned = 0
    for s in sessions:
        findings.extend(audit_session(s))
        turns_scanned += len(s.turns)

    report = build_report(
        findings,
        scope_label=scope_label,
        sessions_scanned=len(sessions),
        turns_scanned=turns_scanned,
        dialog_only_skipped=dialog_only,
        parse_error_turns=parse_errors,
    )

    out_path = Path(args.out) if args.out else Path(settings.sessions_db_path).parent / "audits" / "audit.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("a", encoding="utf-8") as fh:
        fh.write(report)

    print(summarize_stdout(findings, len(sessions), turns_scanned, out_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
