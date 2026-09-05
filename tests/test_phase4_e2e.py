"""Phase 4.2 — 端到端管道测试（mock LLM + 本地 HTML fixture）。

测试目标：在不联网、不调真实 LLM API 的前提下，验证完整管道能跑通：
    用户输入 → Agent mock LLM 决定调 browse → 拿到本地 HTML → mock LLM 决定调
    extract_content → 拿到结构化内容 → mock LLM 决定调 save_record → 写入
    SQLite / 文件 → Agent 返回最终回答

关键：拦截 get_llm() 返回一个 FakeLLM，它按预定义顺序返回：
    1) 带 tool_call 的 AIMessage（让 Agent 去跑某个工具）
    2) 不带 tool_call 的 AIMessage（最终回答，让 Agent 结束）
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


# ---------------------------------------------------------------------------
# Fake LLM — 实现 Runnable 接口，按预定义顺序返回 messages
# ---------------------------------------------------------------------------

class FakeLLM:
    """按队列顺序返回 AIMessage 的假 LLM。"""

    def __init__(self, responses: list[Any]):
        from langchain_core.messages import AIMessage
        self._queue: list[AIMessage] = []
        for r in responses:
            if isinstance(r, str):
                self._queue.append(AIMessage(content=r))
            elif isinstance(r, AIMessage):
                self._queue.append(r)
            elif isinstance(r, dict):
                # {"tool_calls": [...], "content": "..."}
                kwargs = {k: v for k, v in r.items()}
                self._queue.append(AIMessage(**kwargs))
            else:
                self._queue.append(r)
        self._index = 0
        self._bind_tools_called_with: list = []

    def invoke(self, messages, **kwargs):
        r = self._queue[self._index % len(self._queue)]
        self._index += 1
        return r

    def stream(self, messages, **kwargs):
        # 简化：直接 yield 一次完整 AIMessage
        yield self.invoke(messages, **kwargs)

    def bind_tools(self, tools, **kwargs):
        """LangChain create_agent 会对 llm.bind_tools(tools, tool_choice=...)。"""
        self._bind_tools_called_with.append((tools, kwargs))
        return self

    def __call__(self, messages, **kwargs):
        return self.invoke(messages, **kwargs)


# ---------------------------------------------------------------------------
# HTML fixture
# ---------------------------------------------------------------------------

HTML_FIXTURE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <title>示例文章 - CrawAgent E2E 测试</title>
</head>
<body>
  <article>
    <h1>AI 爬虫框架 CrawAgent 发布 1.0</h1>
    <p class="meta">作者: Test Author | 日期: 2026-09-02</p>
    <div class="content">
      <h2>引言</h2>
      <p>CrawAgent 是一个基于 LangChain + LangGraph 的智能爬虫 Agent 框架。</p>
      <h2>核心特性</h2>
      <ul>
        <li>工具插件化：每加一个爬虫只需 10 分钟</li>
        <li>多环境配置：per-profile YAML 覆盖</li>
        <li>定时调度：cron + 内容 hash 变化检测</li>
      </ul>
      <h2>总结</h2>
      <p>Phase 4 完成后即可 docker-compose up 一键部署。</p>
    </div>
  </article>
</body>
</html>"""


@pytest.fixture
def html_server(tmp_path):
    """启动一个简单 http.server 提供本地 HTML fixture。"""
    import http.server
    import threading

    fixture_path = tmp_path / "fixture.html"
    fixture_path.write_text(HTML_FIXTURE, encoding="utf-8")

    class _Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(tmp_path), **kwargs)
        def log_message(self, fmt, *args):
            pass  # 静默

    server = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}/fixture.html"
    finally:
        server.shutdown()


# ---------------------------------------------------------------------------
# E2E 测试：browse_and_crawl → extract → save
# ---------------------------------------------------------------------------

def test_e2e_browse_extract_save(html_server, monkeypatch, tmp_path):
    """
    模拟 LLM 三次决策，跑完整管道：
        1) 调 browse_and_crawl(html_server_url) → 拿到 HTML + markdown
        2) 调 extract_content(...) → 结构化标题/摘要
        3) 调 save_record(...) → 写入 SQLite
        4) 返回最终总结（无 tool_calls → Agent 结束）
    """
    from langchain_core.messages import AIMessage
    import crawagent.graph.agent as agent_mod
    import crawagent.tools.save_tool as save_mod

    fixture_url = html_server

    # —— Step 1: 拦截 get_llm 返回 FakeLLM ——
    # 预定义 4 次 LLM 响应（循环返回也可以，但我们只需要 4 次）
    fake_responses = [
        # 1) 第一次：决定调 browse_and_crawl
        AIMessage(
            content="我先浏览这个页面",
            tool_calls=[{
                "id": "call_browse",
                "name": "browse_and_crawl",
                "args": {"url": fixture_url, "max_depth": 0},
            }],
        ),
        # 2) 第二次：拿到 HTML 后决定调 extract_content
        AIMessage(
            content="页面已获取，现在提取内容",
            tool_calls=[{
                "id": "call_extract",
                "name": "extract_content",
                "args": {"url": fixture_url, "prompt": "提取标题、作者、摘要"},
            }],
        ),
        # 3) 第三次：提取完成后决定 save_record
        AIMessage(
            content="提取完成，保存到数据库",
            tool_calls=[{
                "id": "call_save",
                "name": "save_record",
                "args": {
                    "url": fixture_url,
                    "title": "AI 爬虫框架 CrawAgent 发布 1.0",
                    "content": "# AI 爬虫框架 CrawAgent 发布 1.0\n\n## 引言\n\nCrawAgent 是一个智能爬虫框架。",
                    "platform": "example",
                },
            }],
        ),
        # 4) 第四次：总结并结束（无 tool_calls）
        AIMessage(
            content="已完成抓取、提取和保存。文章标题：AI 爬虫框架 CrawAgent 发布 1.0。",
        ),
    ]

    fake_llm = FakeLLM(fake_responses)

    # —— Step 2: patch 掉 agent 模块的 get_llm ——
    monkeypatch.setattr(agent_mod, "get_llm", lambda model=None: fake_llm)

    # —— Step 3: 隔离 save_tool 的 SQLite 文件 ——
    db_path = tmp_path / "test_sessions.db"
    monkeypatch.setattr(save_mod, "_get_db_path", lambda: str(db_path))
    # 清掉 save_tool 模块级初始化（如果有的话）
    save_mod._init_db()

    # —— Step 4: 启动 Agent 并跑一轮 ——
    agent = agent_mod.get_agent()

    config = {"configurable": {"thread_id": "e2e-test-session"}}
    messages = [{"type": "human", "content": f"帮我抓取 {fixture_url} 这篇文章"}]

    events = []
    for mode, payload in agent.stream({"messages": messages}, config=config, stream_mode=["values", "messages"]):
        if mode == "values":
            events.append(payload)

    # —— Step 5: 验证 Agent 跑完了完整流程 ——
    assert fake_llm._index >= 3, f"FakeLLM 应至少被调 3 次（browse+extract+save），实际 {fake_llm._index}"

    # 最终状态应该有 HumanMessage + 多个 AIMessage（含 tool_calls）+ ToolMessage + 最终 AIMessage
    final = events[-1] if events else {}
    final_msgs = final.get("messages", [])
    assert len(final_msgs) > 4, f"至少有 human + 3 次 AIMessage + 3 次 ToolMessage + 最终 AIMessage"

    # —— Step 6: 验证 save_record 真写进了 SQLite ——
    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.execute("SELECT url, title FROM crawl_records WHERE url LIKE ?", (fixture_url + "%",))
        rows = cur.fetchall()
        assert len(rows) >= 1, f"save_record 应至少写入一条记录，实际 {len(rows)} 条"
        assert rows[0][0].startswith(fixture_url), f"URL 不匹配: {rows[0][0]}"
    finally:
        conn.close()


def test_e2e_no_tool_calls_ends_immediately(html_server, monkeypatch):
    """如果 LLM 第一次就返回最终回答（没 tool_calls），Agent 应立即结束。"""
    from langchain_core.messages import AIMessage
    import crawagent.graph.agent as agent_mod

    fake_llm = FakeLLM([AIMessage(content="这个页面没什么可爬的，跳过。")])
    monkeypatch.setattr(agent_mod, "get_llm", lambda model=None: fake_llm)

    agent = agent_mod.get_agent()
    config = {"configurable": {"thread_id": "e2e-simple-session"}}

    final = None
    for mode, payload in agent.stream(
        {"messages": [{"type": "human", "content": "帮我看看这个页面"}]},
        config=config,
        stream_mode=["values"],
    ):
        final = payload

    assert final is not None, "Agent 应至少产生一个事件"
    # FakeLLM 只被调了一次
    assert fake_llm._index == 1
    # 最终状态只有 human + ai（没有工具调用痕迹）
    msgs = final.get("messages", [])
    assert len(msgs) == 2, f"应该只有 human + ai，实际 {len(msgs)}"
