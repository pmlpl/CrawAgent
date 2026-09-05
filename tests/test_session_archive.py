"""冒烟测试：会话归档/删除相关函数正确可导入、逻辑可调用。

全部离线，不发网络请求。
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def test_archive_constant_exists():
    """_ARCHIVE_SUMMARY_PROMPT 模块级常量存在且含关键字。"""
    from crawagent.web.routers.sessions import _ARCHIVE_SUMMARY_PROMPT
    assert "会话总结" in _ARCHIVE_SUMMARY_PROMPT or "总结" in _ARCHIVE_SUMMARY_PROMPT
    assert "=== 会话记录 ===" in _ARCHIVE_SUMMARY_PROMPT


def test_reconstruct_status_with_real_messages():
    """_reconstruct_status 能从 messages 重建状态栏。"""
    from crawagent.web.routers.sessions import _reconstruct_status
    from langchain_core.messages import HumanMessage, AIMessage, ToolMessage

    msgs = [
        HumanMessage(content="帮我爬一个站点"),
        AIMessage(content="好的", tool_calls=[{"name": "crawl_webpage", "args": {"url": "https://example.com"}, "id": "tc1"}]),
        ToolMessage(content="<html>...</html>", tool_call_id="tc1"),
        HumanMessage(content="提取正文"),
        AIMessage(content="已保存"),
    ]
    status = _reconstruct_status(msgs)
    assert status is not None
    assert "2 轮" in status  # 2 个 HumanMessage
    assert "缓存命中" in status


def test_reconstruct_status_empty_returns_none():
    """空消息列表返回 None。"""
    from crawagent.web.routers.sessions import _reconstruct_status
    assert _reconstruct_status([]) is None


def test_reconstruct_status_all_tools_no_human():
    """只有 ToolMessage 也会返回状态（turn_count+llm_count+tool_count 不全为 0）。"""
    from crawagent.web.routers.sessions import _reconstruct_status
    from langchain_core.messages import ToolMessage
    # 只有 tool_count=1，不全为 0 → 返回状态而非 None
    status = _reconstruct_status([ToolMessage(content="ok", tool_call_id="tc1")])
    assert status is not None
    assert "0 轮" in status


def test_archive_function_exists():
    """_archive_session_sync / _delete_session_sync / _background_summarize 可导入。"""
    from crawagent.web.routers.sessions import (
        _archive_session_sync,
        _delete_session_sync,
        _background_summarize,
    )
    assert callable(_archive_session_sync)
    assert callable(_delete_session_sync)
    assert callable(_background_summarize)


def test_archive_placeholder_json_roundtrip():
    """归档 placeholder JSON 格式能被 json 正确序列化/反序列化。"""
    data = {
        "session_id": "test-session-123",
        "archived_at": "20260902_120000",
        "_compressed_dump": ["[USER] 你好", "[AI] 好的 (工具调用: crawl_webpage)", "[TOOL] <html>..."],
        "original_msg_count": 5,
        "deduped": 0,
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        path = f.name
    with open(path, "r", encoding="utf-8") as f:
        loaded = json.load(f)
    assert loaded["session_id"] == "test-session-123"
    assert "_compressed_dump" in loaded
    Path(path).unlink()
