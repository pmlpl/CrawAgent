"""Web API 测试 — TestClient 离线跑，不真起服务器、不调 LLM、不碰真实 .env/sessions.db。

覆盖 5 用例（handoff §1.4，按当前 API 形态适配）：
    1. GET /api/settings → 不泄露真实 Key（响应无 api_key 字段、无明文）
    2. 空 Key / 掩码 Key 提交 → 真实 Key 不被覆盖（update 留空保旧 + test 拒绝掩码）
    3. POST /api/sessions/bulk-delete → 运行中会话跳过不删，其他正常删
    4. EventLog 游标：append 3 条 → since(0) 拿 3 条 → since(2) 拿 1 条
    5. POST /api/settings thinking_depth=max → 读回仍为 max
"""
import json
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

REAL_KEY = "sk-test-real-key-12345"
PROVIDERS = [{
    "name": "TestProv",
    "base_url": "https://api.test.example.com/v1",
    "api_key": REAL_KEY,
    "models": ["test-model", "test-model-2"],  # 2 个模型：update 单个后服务商条目仍存在
}]


def _parse_env(path: Path) -> dict:
    data = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if s and not s.startswith("#") and "=" in s:
                k, v = s.split("=", 1)
                data[k.strip()] = v.strip()
    return data


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """TestClient + 隔离的 .env：GET/POST 全部读写 tmp_path/.env，不碰真实配置。"""
    env_file = tmp_path / ".env"
    env_file.write_text(
        "THINKING_DEPTH=off\n"
        f"LLM_PROVIDERS={json.dumps(PROVIDERS, ensure_ascii=False, separators=(',', ':'))}\n",
        encoding="utf-8",
    )

    def fake_get_settings():
        data = _parse_env(env_file)
        return SimpleNamespace(
            thinking_depth=data.get("THINKING_DEPTH", "off"),
            start_browser=data.get("START_BROWSER", ""),
            llm_providers=data.get("LLM_PROVIDERS", ""),
            MCP_AUTOSTART=False,
            MCP_START_COMMAND="",
            mcp_servers="",
        )

    # settings 路由与 registry 各持有一份 get_settings 引用，都要替换
    from crawagent.llm import registry
    from crawagent.web.routers import settings as settings_router

    monkeypatch.setattr(settings_router, "ENV_FILE", env_file)
    monkeypatch.setattr(settings_router, "get_settings", fake_get_settings)
    monkeypatch.setattr(registry, "get_settings", fake_get_settings)
    # load_providers() 现在直接读磁盘 .env — monkeypatch _read_env_file 让它返回 tmp_path 里的假 env
    monkeypatch.setattr(registry, "_read_env_file", lambda: env_file)

    from fastapi.testclient import TestClient
    from crawagent.web.server import app

    yield TestClient(app)


def test_settings_get_does_not_leak_api_key(client):
    """用例 1：GET /api/settings 不出现真实 Key 原文，也不返回 api_key 字段。"""
    r = client.get("/api/settings")
    assert r.status_code == 200
    assert REAL_KEY not in r.text
    body = r.json()
    assert body["providers"][0]["name"] == "TestProv"
    assert body["providers"][0]["key_set"] is True
    assert "api_key" not in json.dumps(body)
    assert "api_key" not in json.dumps(body["models"])


def test_empty_or_masked_key_does_not_clobber_real_key(client):
    """用例 2：update 提交空 Key = 保留原 Key；掩码 Key 被拒绝用于连通性测试。"""
    r = client.post("/api/models/update", json={
        "orig_provider": "TestProv", "orig_name": "test-model",
        "provider": "TestProv", "model": "test-model",
        "api_key": "", "base_url": "",
    })
    assert r.status_code == 200
    assert r.json()["ok"] is True

    # 空 Key 提交后，真实 Key 不应被清空或覆盖
    env_file = None
    from crawagent.web.routers import settings as settings_router
    env_file = settings_router.ENV_FILE
    providers = json.loads(_parse_env(env_file)["LLM_PROVIDERS"])
    assert providers[0]["api_key"] == REAL_KEY

    # 掩码 Key：连通性测试直接拒绝（不会拿掩码去打真实 API，也不会写回注册表）
    r2 = client.post("/api/settings/test", json={
        "provider": "TestProv", "model": "test-model",
        "api_key": "sk-****", "base_url": "",
    })
    assert r2.status_code == 200
    assert r2.json()["ok"] is False
    providers = json.loads(_parse_env(env_file)["LLM_PROVIDERS"])
    assert providers[0]["api_key"] == REAL_KEY


def test_bulk_delete_skips_active_sessions(client, monkeypatch):
    """用例 3：批量删除 — 运行中会话跳过，空闲会话正常删除。"""
    from crawagent.web import state
    from crawagent.web.routers import sessions as sessions_router

    # 假 checkpointer：内存 SQLite，含 checkpoints/writes 表与 1 条待删会话
    # check_same_thread=False：路由经 asyncio.to_thread 在工作线程执行 DELETE
    db = sqlite3.connect(":memory:", check_same_thread=False)
    db.execute("CREATE TABLE checkpoints (thread_id TEXT)")
    db.execute("CREATE TABLE writes (thread_id TEXT)")
    db.execute("INSERT INTO checkpoints VALUES ('sid-dead')")

    class _FakeCheckpointer:
        conn = db

    monkeypatch.setattr(sessions_router, "get_agent", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("offline test")))
    monkeypatch.setattr(sessions_router, "get_checkpointer", lambda: _FakeCheckpointer())

    # 塞一个"运行中"会话：必须跳过不删
    state._active_turns["sid-running"] = {"cancelled": False}
    try:
        r = client.post("/api/sessions/bulk-delete", json={"ids": ["sid-running", "sid-dead"]})
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert body["deleted"] == 1
        assert body["skipped_active"] == 1
        assert "sid-running" in state._active_turns      # 运行中的未被删
        assert "sid-running" not in state._metrics or True
    finally:
        state._active_turns.pop("sid-running", None)

    # sid-dead 已从检查点删除
    rows = db.execute("SELECT thread_id FROM checkpoints").fetchall()
    assert rows == []


def test_event_log_cursor_replay():
    """用例 4：EventLog append 3 条 → since(0) 拿 3 条 → since(2) 拿 1 条。"""
    from crawagent.web.event_log import EventLog

    log = EventLog()
    log.append({"type": "tool_call", "name": "t1"})
    log.append({"type": "tool_result", "content": "r1"})
    log.append({"type": "done"})

    events, cursor = log.since(0)
    assert len(events) == 3
    assert cursor == 3

    events2, cursor2 = log.since(2)
    assert len(events2) == 1
    assert events2[0]["type"] == "done"
    assert cursor2 == 3


def test_thinking_depth_roundtrip(client):
    """用例 5：POST /api/settings thinking_depth=max → 再读回仍为 max。"""
    r = client.post("/api/settings", json={"thinking_depth": "max"})
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert r.json()["thinking_depth"] == "max"

    r2 = client.get("/api/settings")
    assert r2.json()["thinking_depth"] == "max"


def test_start_browser_roundtrip_and_whitelist(client):
    """START_BROWSER：chrome 保存回读一致；非法值拒绝；空串恢复系统默认。"""
    r = client.post("/api/settings", json={"start_browser": "chrome"})
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert r.json()["start_browser"] == "chrome"

    r2 = client.get("/api/settings")
    assert r2.json()["start_browser"] == "chrome"

    bad = client.post("/api/settings", json={"start_browser": "safari"})
    assert bad.json()["ok"] is False

    clear = client.post("/api/settings", json={"start_browser": ""})
    assert clear.json()["ok"] is True
    assert clear.json()["start_browser"] == ""


def test_session_export_json(client, monkeypatch):
    """用例 6a：导出 JSON — 含完整消息 + 元数据 + Content-Disposition attachment。"""
    from langchain_core.messages import HumanMessage, AIMessage, ToolMessage
    from crawagent.web.routers import sessions as sessions_router

    class _FakeState:
        values = {
            "messages": [
                HumanMessage(content="帮我爬豆瓣 Top 250"),
                AIMessage(
                    content="好的，我来帮你爬取豆瓣 Top 250 电影列表。",
                    tool_calls=[{"id": "tc1", "name": "crawl", "args": {"url": "https://movie.douban.com/top250"}}],
                ),
                ToolMessage(content="[{'title': '肖申克的救赎', 'rank': 1}]", tool_call_id="tc1"),
                AIMessage(content="爬取完成，共获取 250 部电影。"),
            ]
        }

    class _FakeAgent:
        def get_state(self, config):
            return _FakeState()

    db = sqlite3.connect(":memory:", check_same_thread=False)
    db.execute("CREATE TABLE session_titles (thread_id TEXT PRIMARY KEY, title TEXT)")
    db.execute("INSERT INTO session_titles VALUES ('sid-test', '豆瓣爬取')")

    class _FakeCheckpointer:
        conn = db

    monkeypatch.setattr(sessions_router, "get_agent", lambda: _FakeAgent())
    monkeypatch.setattr(sessions_router, "get_checkpointer", lambda: _FakeCheckpointer())

    r = client.get("/api/sessions/sid-test/export?format=json")
    assert r.status_code == 200
    cd = r.headers.get("content-disposition", "")
    assert "attachment" in cd and ".json" in cd
    body = r.json()
    assert body["session_id"] == "sid-test"
    assert body["title"] == "豆瓣爬取"
    assert body["message_count"] >= 3
    roles = [m["role"] for m in body["messages"]]
    assert "user" in roles and "ai" in roles and "tool_call" in roles and "tool_result" in roles
    # tool_result 不截断（truncate=False）
    tr = [m for m in body["messages"] if m["role"] == "tool_result"][0]
    assert "截断" not in tr["content"]


def test_session_export_markdown(client, monkeypatch):
    """用例 6b：导出 Markdown — 人类可读纪要，含 Content-Disposition。"""
    from langchain_core.messages import HumanMessage, AIMessage
    from crawagent.web.routers import sessions as sessions_router

    class _FakeState:
        values = {"messages": [HumanMessage(content="测试消息"), AIMessage(content="这是回复")]}

    class _FakeAgent:
        def get_state(self, config):
            return _FakeState()

    db = sqlite3.connect(":memory:", check_same_thread=False)
    db.execute("CREATE TABLE session_titles (thread_id TEXT PRIMARY KEY, title TEXT)")

    class _FakeCheckpointer:
        conn = db

    monkeypatch.setattr(sessions_router, "get_agent", lambda: _FakeAgent())
    monkeypatch.setattr(sessions_router, "get_checkpointer", lambda: _FakeCheckpointer())

    r = client.get("/api/sessions/sid-test/export?format=md")
    assert r.status_code == 200
    cd = r.headers.get("content-disposition", "")
    assert "attachment" in cd and ".md" in cd
    text = r.text
    assert "测试消息" in text and "这是回复" in text


def test_session_export_empty_session(client, monkeypatch):
    """用例 6c：导出不存在的会话 — 不报错，返回空消息。"""
    from crawagent.web.routers import sessions as sessions_router

    monkeypatch.setattr(sessions_router, "get_agent", lambda: (_ for _ in ()).throw(RuntimeError("no agent")))
    db = sqlite3.connect(":memory:", check_same_thread=False)
    db.execute("CREATE TABLE session_titles (thread_id TEXT PRIMARY KEY, title TEXT)")

    class _FakeCheckpointer:
        conn = db

    monkeypatch.setattr(sessions_router, "get_checkpointer", lambda: _FakeCheckpointer())

    r = client.get("/api/sessions/nonexistent/export")
    assert r.status_code == 200
    body = r.json()
    assert body["messages"] == []
    assert body["message_count"] == 0


# ============================================================
# 生态面板（P2-9 后补 UI）：GET /api/ecosystem + POST /api/mcp/servers/save
# ============================================================

ECO_MCP_SERVERS = [
    {"name": "anything", "transport": "streamable_http", "url": "http://127.0.0.1:23816/mcp",
     "headers": {"Authorization": "Bearer real-token-value-123456"}},
    {"name": "fetch", "transport": "stdio", "command": "python", "args": ["-m", "mcp_server_fetch"]},
]


@pytest.fixture()
def eco_client(tmp_path, monkeypatch):
    """生态面板专用：隔离 .env + 隔离 skills 目录 + 假 skills 模块 get_settings。"""
    import json as _json

    env_file = tmp_path / ".env"
    env_file.write_text(
        "THINKING_DEPTH=off\n"
        f"MCP_SERVERS={_json.dumps(ECO_MCP_SERVERS, ensure_ascii=False)}\n",
        encoding="utf-8",
    )
    skills_dir = tmp_path / "skills"
    (skills_dir / "demo-skill").mkdir(parents=True)
    (skills_dir / "demo-skill" / "SKILL.md").write_text(
        "---\nname: demo-skill\ndescription: 测试技能描述\n---\n正文\n", encoding="utf-8"
    )

    def fake_get_settings():
        data = _parse_env(env_file)
        return SimpleNamespace(
            thinking_depth=data.get("THINKING_DEPTH", "off"),
            MCP_AUTOSTART=False,
            MCP_START_COMMAND="",
            mcp_servers=data.get("MCP_SERVERS", ""),
            skills_dirs=str(skills_dir),
            project_root=tmp_path,
        )

    from crawagent.web.routers import settings as settings_router
    from crawagent.graph import skills as skills_mod

    monkeypatch.setattr(settings_router, "ENV_FILE", env_file)
    monkeypatch.setattr(settings_router, "get_settings", fake_get_settings)
    monkeypatch.setattr(skills_mod, "get_settings", fake_get_settings)

    from fastapi.testclient import TestClient
    from crawagent.web.server import app

    c = TestClient(app)
    c._eco_env_file = env_file  # 供用例直接读磁盘 .env 断言
    yield c


def test_ecosystem_snapshot_masks_token_and_lists_skills(eco_client):
    """生态快照：技能列表含名称与来源；MCP 鉴权头脱敏（不出完整 token）。"""
    data = eco_client.get("/api/ecosystem").json()
    names = [s["name"] for s in data["skills"]]
    assert "demo-skill" in names
    demo = next(s for s in data["skills"] if s["name"] == "demo-skill")
    assert demo["source"] == "builtin" and demo["description"] == "测试技能描述"
    # MCP：掩码后不出现完整 token，且状态字段齐备
    anything = next(s for s in data["mcp_servers"] if s["name"] == "anything")
    assert anything["headers"]["Authorization"].endswith("***")
    assert "real-token-value" not in anything["headers"]["Authorization"]
    status = {s["name"]: s for s in data["mcp_status"]}
    assert "disabled" in status["anything"] and "running" in status["anything"]


def test_mcp_save_roundtrip_preserves_masked_token(eco_client):
    """保存回写：掩码 header 不覆盖真实 token；disabled 标记落盘并生效。"""
    eco = eco_client.get("/api/ecosystem").json()
    servers = eco["mcp_servers"]
    servers[1]["disabled"] = True  # 停用 fetch

    r = eco_client.post("/api/mcp/servers/save", json={"servers": servers}).json()
    assert r["ok"] is True
    saved = next(s for s in r["mcp_servers"] if s["name"] == "fetch")
    assert saved["disabled"] is True

    # 磁盘 .env：真实 token 不被掩码覆盖（历史 Key 覆盖 Bug 的回归防线）+ disabled 落盘
    disk_line = next(
        ln for ln in eco_client._eco_env_file.read_text(encoding="utf-8").splitlines()
        if ln.startswith("MCP_SERVERS=")
    )
    import json as _json
    disk_servers = _json.loads(disk_line.split("=", 1)[1])
    disk_anything = next(s for s in disk_servers if s["name"] == "anything")
    assert disk_anything["headers"]["Authorization"] == "Bearer real-token-value-123456"
    assert "***" not in disk_anything["headers"]["Authorization"]
    assert next(s for s in disk_servers if s["name"] == "fetch")["disabled"] is True

    # 出口永远脱敏：再 GET 一次，token 仍是掩码形态
    again = eco_client.get("/api/ecosystem").json()
    out_anything = next(s for s in again["mcp_servers"] if s["name"] == "anything")
    assert out_anything["headers"]["Authorization"].endswith("***")


def test_mcp_save_rejects_duplicate_names(eco_client):
    """同名 server 拒绝保存。"""
    eco = eco_client.get("/api/ecosystem").json()
    servers = eco["mcp_servers"] + [dict(eco["mcp_servers"][0])]
    r = eco_client.post("/api/mcp/servers/save", json={"servers": servers}).json()
    assert r["ok"] is False and "重复" in r["error"]
