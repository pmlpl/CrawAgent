"""全局测试隔离（tests/conftest.py）。

变更 039 发现两类跨测试污染，autouse fixture 一并根治：

1. ContextVar 跨测试泄漏：save_tool._current_session / graph.agent
   .ctx_session_id set 之后不随 monkeypatch 还原，前面的测试设了会话 id，
   后面的测试调 save_record 就带着别人的会话跑。每个测试前显式清空、
   测试后还原。

2. save_record 档案双写落进真实项目：只要测试内部自己 set 了会话 id
   （如 test_knowledge_search 的 "s1"），save_record 的 039 双写就会把
   md 快照写进真实 data/sessions/<sid>/（2026-10-01 全量跑实测污染）。
   把 session_folder.get_settings patch 到本测试专属 tmp_path，
   双写永不碰真实 data/。
"""
import pytest


@pytest.fixture(autouse=True)
def _isolate_session_context(tmp_path, monkeypatch):
    from crawagent.graph.agent import ctx_session_id
    from crawagent.tools import save_tool
    from crawagent.tools import session_folder as sf

    class _IsoSettings:
        project_root = tmp_path

    tok_save = save_tool._current_session.set("")
    tok_ctx = ctx_session_id.set("")
    monkeypatch.setattr(sf, "get_settings", lambda: _IsoSettings())
    yield
    save_tool._current_session.reset(tok_save)
    ctx_session_id.reset(tok_ctx)
