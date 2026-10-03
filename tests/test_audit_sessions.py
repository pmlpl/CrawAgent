"""042 会话行为审计 — 桩会话双向测试。

覆盖（规格 §五.2）：
- 五条规则各 1 次命中 + 各自合法例外不误报；
- 异常输入（空消息、非 list、未知类型、孤儿 ToolMessage）不崩且计数；
- load_sessions 端到端：临时 checkpointer 播种 → 只读装载 → 时间窗过滤 →
  纯对话跳过；main() CLI 报告追加落盘。
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.base import empty_checkpoint
from langgraph.checkpoint.sqlite import SqliteSaver

from scripts.audit_sessions import (  # noqa: E402
    build_report,
    audit_session,
    load_sessions,
    main,
    parse_messages,
)

# ---------------------------------------------------------------------------
# 消息构造助手
# ---------------------------------------------------------------------------

_seq = iter(range(10000))


def _human(text: str) -> HumanMessage:
    return HumanMessage(content=text)


def _ai(*calls: tuple[str, dict]) -> AIMessage:
    tcs = [
        {"name": name, "args": args, "id": f"call_{next(_seq)}"}
        for name, args in calls
    ]
    return AIMessage(content="", tool_calls=tcs)


def _tool(ai: AIMessage, idx: int, content: str) -> ToolMessage:
    return ToolMessage(content=content, tool_call_id=ai.tool_calls[idx]["id"])


def _session(messages: list, tid: str = "t-test", title: str = "测试会话"):
    rec = parse_messages(messages)
    rec.thread_id = tid
    rec.title = title
    return rec


# ---------------------------------------------------------------------------
# R1 脚本先行无档案
# ---------------------------------------------------------------------------


def test_r1_hit_script_without_profile_or_failure():
    ai = _ai(("run_custom_script", {"code": "import requests\nprint(requests.get('https://example.com/data').text)"}))
    msgs = [_human("帮我爬 https://example.com 的数据"), ai, _tool(ai, 0, "ok data")]
    findings = [f for f in audit_session(_session(msgs)) if f.rule == "R1"]
    assert len(findings) == 1
    assert findings[0].confidence == "high"
    assert findings[0].turn == 1


def test_r1_exempt_when_profile_checked_first():
    ai1 = _ai(("list_site_profiles", {"origin": "https://example.com"}))
    ai2 = _ai(("run_custom_script", {"code": "print(requests.get('https://example.com').text)"}))
    msgs = [
        _human("爬 https://example.com"),
        ai1, _tool(ai1, 0, "profile found"),
        ai2, _tool(ai2, 0, "ok"),
    ]
    assert [f for f in audit_session(_session(msgs)) if f.rule == "R1"] == []


def test_r1_exempt_when_same_domain_failure_first():
    ai1 = _ai(("crawl_webpage", {"url": "https://example.com/page"}))
    ai2 = _ai(("run_custom_script", {"code": "print(requests.get('https://example.com/page').text)"}))
    msgs = [
        _human("爬 https://example.com"),
        ai1, _tool(ai1, 0, "ERROR: Crawl failed for https://example.com/page — timeout"),
        ai2, _tool(ai2, 0, "ok"),
    ]
    assert [f for f in audit_session(_session(msgs)) if f.rule == "R1"] == []


def test_r1_cross_domain_failure_downgrades_to_low():
    ai1 = _ai(("crawl_webpage", {"url": "https://aaa.com/page"}))
    ai2 = _ai(("run_custom_script", {"code": "print(requests.get('https://bbb.com/data').text)"}))
    msgs = [
        _human("抓数据"),
        ai1, _tool(ai1, 0, "ERROR: Crawl failed for https://aaa.com/page — timeout"),
        ai2, _tool(ai2, 0, "ok"),
    ]
    findings = [f for f in audit_session(_session(msgs)) if f.rule == "R1"]
    assert len(findings) == 1
    assert findings[0].confidence == "low"


# ---------------------------------------------------------------------------
# R2 抓完不问档
# ---------------------------------------------------------------------------

_LIST_OK = "List (12 items, pages=3): 1. [a](https://x.com/1) 2. [b](https://x.com/2)"


def test_r2_hit_crawl_done_without_ask():
    ai = _ai(("extract_list_paged", {"url": "https://x.com/list"}))
    msgs = [_human("抓全这个列表"), ai, _tool(ai, 0, _LIST_OK), AIMessage(content="列表如上")]
    findings = [f for f in audit_session(_session(msgs)) if f.rule == "R2"]
    assert len(findings) == 1
    assert findings[0].confidence == "high"
    assert "extract_list_paged" in findings[0].evidence


def test_r2_exempt_when_ask_user_in_turn():
    ai = _ai(("extract_list_paged", {"url": "https://x.com/list"}))
    ai2 = _ai(("ask_user", {"question": "要存档案吗？", "options": ["存入档案", "不用"]}))
    msgs = [_human("抓全这个列表"), ai, _tool(ai, 0, _LIST_OK), ai2, _tool(ai2, 0, "存入档案")]
    assert [f for f in audit_session(_session(msgs)) if f.rule == "R2"] == []


def test_r2_exempt_when_save_record_in_turn():
    ai = _ai(("extract_list_paged", {"url": "https://x.com/list"}))
    ai2 = _ai(("save_record", {"url": "https://x.com", "title": "t", "content": "c"}))
    msgs = [_human("抓全这个列表"), ai, _tool(ai, 0, _LIST_OK), ai2, _tool(ai2, 0, '{"ok": true}')]
    assert [f for f in audit_session(_session(msgs)) if f.rule == "R2"] == []


def test_r2_few_items_not_a_trigger():
    ai = _ai(("extract_list_paged", {"url": "https://x.com/list"}))
    msgs = [_human("看看这个列表"), ai, _tool(ai, 0, "List (3 items, pages=1): 1. [a](https://x.com/1)")]
    assert [f for f in audit_session(_session(msgs)) if f.rule == "R2"] == []


def test_r2_exempt_user_declined_in_request():
    ai = _ai(("extract_list_paged", {"url": "https://x.com/list"}))
    msgs = [_human("抓全这个列表，不用存档案"), ai, _tool(ai, 0, _LIST_OK)]
    assert [f for f in audit_session(_session(msgs)) if f.rule == "R2"] == []


def test_r2_exempt_no_more_ask_after_user_answer():
    ai1 = _ai(("extract_list_paged", {"url": "https://x.com/list"}))
    ai2 = _ai(("ask_user", {"question": "要存档案吗？", "options": ["存入档案", "不用", "本会话不再询问"]}))
    ai3 = _ai(("extract_list_paged", {"url": "https://y.com/list"}))
    msgs = [
        _human("抓列表"), ai1, _tool(ai1, 0, _LIST_OK), ai2, _tool(ai2, 0, "本会话不再询问"),
        _human("换个站再抓"), ai3, _tool(ai3, 0, "List (9 items, pages=2): 1. [c](https://y.com/3)"),
    ]
    assert [f for f in audit_session(_session(msgs)) if f.rule == "R2"] == []


def test_r2_user_preemptive_decline_only_covers_own_turn():
    # 用户第 2 轮没说不用存，第 1 轮的拒存不外溢 → 第 2 轮仍命中
    ai1 = _ai(("extract_list_paged", {"url": "https://x.com/list"}))
    ai3 = _ai(("extract_list_paged", {"url": "https://y.com/list"}))
    msgs = [
        _human("抓列表，不用存"), ai1, _tool(ai1, 0, _LIST_OK),
        _human("再抓这个"), ai3, _tool(ai3, 0, "List (8 items, pages=2): 1. [c](https://y.com/3)"),
    ]
    findings = [f for f in audit_session(_session(msgs)) if f.rule == "R2"]
    assert len(findings) == 1 and findings[0].turn == 2


def test_r2_media_download_not_a_trigger():
    # 校准结论：download_* / extract_social_media 是用户自取的媒体/元数据，
    # 不是知识库候选，不触发 ARCHIVE-ASK
    ai = _ai(("download_social_media", {"url": "https://www.bilibili.com/video/BV1xx"}))
    msgs = [_human("下载原视频并保存"), ai, _tool(ai, 0, '{"platform": "bilibili", "output_dir": "X"}')]
    assert [f for f in audit_session(_session(msgs)) if f.rule == "R2"] == []


def test_r2_requires_turn_ending_on_crawl():
    # 抓完还继续写脚本加工 = 任务未收尾，不算「抓完就结束回合」
    ai1 = _ai(("extract_list_paged", {"url": "https://x.com/list"}))
    ai2 = _ai(("run_custom_script", {"code": "print('加工')"}))
    msgs = [
        _human("抓列表做成表格"), ai1, _tool(ai1, 0, _LIST_OK), ai2, _tool(ai2, 0, "done"),
    ]
    assert [f for f in audit_session(_session(msgs)) if f.rule == "R2"] == []


def test_r2_auth_wall_is_not_a_success():
    # 校准结论：返回「需要登录」= 内容提取被认证墙拦截，无可用内容
    ai = _ai(("browser_use_navigate", {"url": "https://search.jd.com/x", "task": "抓价格"}))
    msgs = [_human("看下价格"), ai, _tool(ai, 0, "需要登录  京东搜索页面跳转到了登录页面（passport.jd.com）")]
    assert [f for f in audit_session(_session(msgs)) if f.rule == "R2"] == []


def test_r1_exempt_failure_without_extractable_domains():
    # 校准结论：markitdown 本地文件失败 → 图片分析脚本，两侧都抽不出域名
    # → 视为同一任务的失败在先，合规（此前误降 low）
    ai1 = _ai(("markitdown_convert", {"file_path": "C:/pics/动漫少女.jpg"}))
    ai2 = _ai(("run_custom_script", {"code": "from PIL import Image; print(Image.open('动漫少女.jpg').size)"}))
    msgs = [
        _human("帮我分析这张图片"), ai1,
        _tool(ai1, 0, "[ERROR] 转换结果为空（文件可能加密/扫描件/不支持）: 动漫少女.jpg"),
        ai2, _tool(ai2, 0, "size: (2730, 1536)"),
    ]
    assert [f for f in audit_session(_session(msgs)) if f.rule == "R1"] == []


def test_r1_exempt_user_requested_script():
    ai = _ai(("run_custom_script", {"code": "print(requests.get('https://httpbin.org/json').text)"}))
    msgs = [
        _human("写一个脚本，用 requests 获取 https://httpbin.org/json 的内容并打印解析后的 JSON"),
        ai, _tool(ai, 0, '{"slideshow": true}'),
    ]
    assert [f for f in audit_session(_session(msgs)) if f.rule == "R1"] == []


def test_r1_exempt_err_prefix_failure():
    # 校准结论：android 工具的失败前缀是「ERR:」，此前漏配导致误报
    ai1 = _ai(("list_adb_devices", {}))
    ai2 = _ai(("run_custom_script", {"code": "print('check adb path')"}))
    msgs = [
        _human("逆向这个手机App"), ai1, _tool(ai1, 0, "ERR: adb 未安装（装 platform-tools 后重试）"),
        ai2, _tool(ai2, 0, "OS: win32 adb not found"),
    ]
    assert [f for f in audit_session(_session(msgs)) if f.rule == "R1"] == []


# ---------------------------------------------------------------------------
# R3 同参数重试
# ---------------------------------------------------------------------------


def test_r3_hit_identical_args_after_failure():
    code = "print(requests.get('https://x.com').text)"
    ai1 = _ai(("run_custom_script", {"code": code}))
    ai2 = _ai(("run_custom_script", {"code": code}))
    msgs = [
        _human("抓 x.com"), ai1, _tool(ai1, 0, "[EXIT CODE 1]\nTraceback ..."),
        ai2, _tool(ai2, 0, "ok"),
    ]
    findings = [f for f in audit_session(_session(msgs)) if f.rule == "R3"]
    assert len(findings) == 1
    assert findings[0].confidence == "high"


def test_r3_exempt_when_previous_success():
    code = "print(requests.get('https://x.com').text)"
    ai1 = _ai(("run_custom_script", {"code": code}))
    ai2 = _ai(("run_custom_script", {"code": code}))
    msgs = [_human("抓 x.com"), ai1, _tool(ai1, 0, "data ok"), ai2, _tool(ai2, 0, "data ok")]
    assert [f for f in audit_session(_session(msgs)) if f.rule == "R3"] == []


def test_r3_args_differ_no_hit():
    ai1 = _ai(("run_custom_script", {"code": "print(1)"}))
    ai2 = _ai(("run_custom_script", {"code": "print(2)"}))
    msgs = [
        _human("抓数据"), ai1, _tool(ai1, 0, "[EXIT CODE 1]\nboom"),
        ai2, _tool(ai2, 0, "ok"),
    ]
    assert [f for f in audit_session(_session(msgs)) if f.rule == "R3"] == []


# ---------------------------------------------------------------------------
# R4 MCP 加前不查重
# ---------------------------------------------------------------------------

_CFG = '{"command": "node", "args": ["server.js"]}'


def test_r4_hit_add_without_list():
    ai = _ai(("add_mcp_server", {"name": "foo", "config": _CFG}))
    msgs = [_human("加个 MCP"), ai, _tool(ai, 0, "added")]
    findings = [f for f in audit_session(_session(msgs)) if f.rule == "R4"]
    assert len(findings) == 1 and findings[0].confidence == "high"


def test_r4_exempt_list_first():
    ai1 = _ai(("list_mcp_servers", {}))
    ai2 = _ai(("add_mcp_server", {"name": "foo", "config": _CFG}))
    msgs = [_human("加个 MCP"), ai1, _tool(ai1, 0, "0 servers"), ai2, _tool(ai2, 0, "added")]
    assert [f for f in audit_session(_session(msgs)) if f.rule == "R4"] == []


# ---------------------------------------------------------------------------
# R5 Cookie 不索取
# ---------------------------------------------------------------------------

_COOKIE_ERR = "[WEREAD_COOKIE_NOT_SET] 微信读书需要登录 Cookie。请提供 wr_vid 与 wr_ssk。"


def test_r5_hit_recall_without_ask():
    ai1 = _ai(("list_weread_chapters", {"book_id": "abc"}))
    ai2 = _ai(("get_weread_chapter", {"book_id": "abc"}))
    msgs = [
        _human("看这本书的章节"), ai1, _tool(ai1, 0, _COOKIE_ERR),
        ai2, _tool(ai2, 0, _COOKIE_ERR),
    ]
    findings = [f for f in audit_session(_session(msgs)) if f.rule == "R5"]
    assert len(findings) == 1
    assert findings[0].confidence == "low"


def test_r5_exempt_ask_between_calls():
    ai1 = _ai(("list_weread_chapters", {"book_id": "abc"}))
    ai2 = _ai(("ask_user", {"question": "请提供 Cookie", "options": ["好的", "算了"]}))
    ai3 = _ai(("list_weread_chapters", {"book_id": "abc"}))
    msgs = [
        _human("看这本书"), ai1, _tool(ai1, 0, _COOKIE_ERR),
        ai2, _tool(ai2, 0, "Cookie 已发"),
        ai3, _tool(ai3, 0, "chapters ok"),
    ]
    assert [f for f in audit_session(_session(msgs)) if f.rule == "R5"] == []


# ---------------------------------------------------------------------------
# 异常输入
# ---------------------------------------------------------------------------


def test_parse_empty_and_anomalous_inputs():
    # 空消息
    rec = parse_messages([])
    assert rec.turns == [] and rec.parse_error_turns == 0
    # 非 list
    rec = parse_messages(object())
    assert rec.parse_error_turns == 1
    # 未知类型消息（含 SystemMessage 之类）跳过且计数
    rec = parse_messages([_human("hi"), "不是消息对象", _ai(("save_record", {"url": "u", "title": "t", "content": "c"}))])
    assert len(rec.turns) == 1 and rec.parse_error_turns == 1
    # 孤儿 ToolMessage
    orphan = ToolMessage(content="x", tool_call_id="no-such-call")
    rec = parse_messages([_human("hi"), orphan])
    assert rec.parse_error_turns == 1
    # 结果异常不产生 findings 崩溃
    assert audit_session(rec) == []


def test_audit_session_on_dialog_only():
    rec = _session([_human("你好"), AIMessage(content="你好！")])
    assert rec.calls == []
    assert audit_session(rec) == []


# ---------------------------------------------------------------------------
# 端到端：临时 checkpointer 播种 → 装载 → CLI
# ---------------------------------------------------------------------------


def _seed(db_path: Path, thread_id: str, messages: list, ts: str | None = None) -> None:
    conn = sqlite3.connect(str(db_path))
    try:
        saver = SqliteSaver(conn)
        saver.setup()
        ckpt = empty_checkpoint()
        if ts:
            ckpt["ts"] = ts
        ckpt["channel_values"] = {"messages": tuple(messages)}
        saver.put(
            {"configurable": {"thread_id": thread_id, "checkpoint_ns": "", "checkpoint_id": None}},
            ckpt,
            {"source": "input", "step": 1, "writes": {}},
            {},
        )
    finally:
        conn.close()


def _violating_msgs() -> list:
    ai = _ai(("run_custom_script", {"code": "print(requests.get('https://bad.com').text)"}))
    return [_human("爬 https://bad.com"), ai, _tool(ai, 0, "ok")]


def _clean_msgs() -> list:
    ai1 = _ai(("list_site_profiles", {"origin": "https://bad.com"}))
    ai2 = _ai(("run_custom_script", {"code": "print('archived code')"}))
    return [
        _human("爬 https://bad.com"),
        ai1, _tool(ai1, 0, "profile found"),
        ai2, _tool(ai2, 0, "ok"),
    ]


def _settings(tmp_path: Path) -> SimpleNamespace:
    return SimpleNamespace(
        sessions_db_path=tmp_path / "sessions.db",
        checkpoint_backend="sqlite",
    )


def _title_fn(tid: str, _settings=None) -> str:
    return f"标题-{tid[:6]}"


def test_load_sessions_filters_and_skips(tmp_path):
    db = tmp_path / "sessions.db"
    _seed(db, "bad-run", _violating_msgs())
    _seed(db, "clean-run", _clean_msgs())
    _seed(db, "chat-only", [_human("你好"), AIMessage(content="嗨")])
    _seed(db, "old-run", _violating_msgs(), ts="2020-01-01T00:00:00+00:00")

    sessions, parse_errors, dialog_only = load_sessions(
        _settings(tmp_path), all_scopes=True, title_fn=_title_fn
    )
    ids = {s.thread_id for s in sessions}
    assert ids == {"bad-run", "clean-run", "old-run"}  # all_scopes 不限时间
    assert dialog_only == 1 and parse_errors == 0
    assert all(s.title.startswith("标题-") for s in sessions)

    # 时间窗：默认 7 天排除 2020 年的老会话
    sessions_7d, _, _ = load_sessions(_settings(tmp_path), days=7, title_fn=_title_fn)
    assert {s.thread_id for s in sessions_7d} == {"bad-run", "clean-run"}


def test_main_cli_writes_report_and_appends(tmp_path):
    db = tmp_path / "sessions.db"
    _seed(db, "bad-run", _violating_msgs())
    out = tmp_path / "audits" / "audit.md"
    settings = _settings(tmp_path)
    argv = ["--all", "--out", str(out)]

    assert main(argv, settings=settings, title_fn=_title_fn) == 0
    text1 = out.read_text(encoding="utf-8")
    assert "## " in text1 and "### 违规（high）" in text1
    assert "[R1]" in text1 and "bad-run"[:8] in text1
    assert "扫描会话 1 个" in text1

    assert main(argv, settings=settings, title_fn=_title_fn) == 0
    text2 = out.read_text(encoding="utf-8")
    headers = [line for line in text2.splitlines() if line.startswith("## ")]
    assert len(headers) == 2  # 追加式：第二次扫描另起一节


def test_main_cli_sessions_flag_overrides_window(tmp_path):
    db = tmp_path / "sessions.db"
    _seed(db, "old-run", _violating_msgs(), ts="2020-01-01T00:00:00+00:00")
    out = tmp_path / "audit.md"
    settings = _settings(tmp_path)
    argv = ["--days", "7", "--sessions", "old-run", "--out", str(out)]
    assert main(argv, settings=settings, title_fn=_title_fn) == 0
    text = out.read_text(encoding="utf-8")
    assert "old-run"[:8] in text  # 点名的会话不受时间窗限制


def test_main_cli_nonexistent_session_is_safe(tmp_path):
    settings = _settings(tmp_path)
    out = tmp_path / "audit.md"
    argv = ["--sessions", "ghost", "--out", str(out)]
    assert main(argv, settings=settings, title_fn=_title_fn) == 0
    assert "扫描会话 0 个" in out.read_text(encoding="utf-8")


def test_redis_backend_exits_with_code_2(tmp_path):
    settings = SimpleNamespace(
        sessions_db_path=tmp_path / "sessions.db",
        checkpoint_backend="redis",
    )
    assert main(["--all", "--out", str(tmp_path / "a.md")], settings=settings) == 2


def test_build_report_counts_and_skips(tmp_path):
    ai = _ai(("run_custom_script", {"code": "print(1)"}))
    rec = _session([_human("爬"), ai, _tool(ai, 0, "ok")], tid="sid-123456", title='带"引号"的标题')
    findings = audit_session(rec)
    assert findings and all(f.rule in {"R1"} or f.rule for f in findings)
    report = build_report(
        findings,
        scope_label="最近 7 天",
        sessions_scanned=3,
        turns_scanned=9,
        dialog_only_skipped=2,
        parse_error_turns=1,
    )
    assert "### 违规（high）" in report and "### 待判读（low）" in report
    assert "解析失败轮 1 个" in report and "纯对话跳过 2 个" in report
    # 标题里的直引号被替换，不破坏一行式条目
    assert '带"引号"的标题' not in report


def test_json_error_result_counts_as_failure():
    from scripts.audit_sessions import is_failure

    assert is_failure(json.dumps({"error": "unavailable"}, ensure_ascii=False))
    assert not is_failure(json.dumps({"downloaded": ["a.png"]}, ensure_ascii=False))
    assert is_failure("")
    assert not is_failure("List (10 items, pages=2): fine")
