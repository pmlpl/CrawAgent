"""互联网搜索工具测试：解析纯函数 + search 工具 web 分支（单/多引擎）。"""

from crawagent.harness.tools import (
    SEARCH_TOOL,
    _normalize_url_for_dedup,
    _parse_search_results,
    _web_search,
    _web_search_multi,
    create_default_tools,
)

_DDG_HTML = """
<html><body>
<div class="result">
  <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fpage&amp;rut=abc">Example Page</a>
  <a class="result__snippet">This is a snippet text.</a>
</div>
<div class="result">
  <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.org%2Fother&amp;rut=def">Other Page</a>
</div>
<div class="result">
  <a class="result__a" href="https://direct.example.com/no-wrap">Direct Link</a>
  <a class="result__snippet">No wrapper snippet.</a>
</div>
</body></html>
"""

_BING_HTML = """
<html><body>
<li class="b_algo">
  <h2><a href="https://example.com/bing1">Bing Result 1</a></h2>
  <div class="b_caption"><p>Bing snippet one.</p></div>
</li>
<li class="b_algo">
  <h2><a href="https://example.com/bing2">Bing Result 2</a></h2>
</li>
</body></html>
"""


def test_parse_duckduckgo_restores_uddg_url():
    results = _parse_search_results(_DDG_HTML, "duckduckgo", limit=10)
    assert len(results) == 3
    # uddg 包装链接还原为真实 URL
    assert results[0]["url"] == "https://example.com/page"
    assert results[0]["title"] == "Example Page"
    assert results[0]["snippet"] == "This is a snippet text."
    # 无包装链接原样保留
    assert results[2]["url"] == "https://direct.example.com/no-wrap"


def test_parse_bing():
    results = _parse_search_results(_BING_HTML, "bing", limit=10)
    assert len(results) == 2
    assert results[0]["url"] == "https://example.com/bing1"
    assert results[0]["snippet"] == "Bing snippet one."
    assert results[1]["snippet"] == ""


def test_parse_limit():
    results = _parse_search_results(_DDG_HTML, "duckduckgo", limit=1)
    assert len(results) == 1
    assert results[0]["title"] == "Example Page"


async def test_web_search_unknown_engine():
    result = await _web_search("test", engine="yahoo")
    # 未知引擎返回 ok=False 而非抛异常
    assert result["ok"] is False


def test_search_tool_def_has_engine_params():
    props = SEARCH_TOOL.parameters["properties"]
    assert "engine" in props
    assert props["engine"]["enum"] == ["duckduckgo", "bing"]
    assert "engines" in props
    assert props["engines"]["items"]["enum"] == ["duckduckgo", "bing"]


def test_normalize_url_for_dedup():
    assert _normalize_url_for_dedup("https://www.Example.com/Page/") == "https://example.com/Page"
    assert _normalize_url_for_dedup("http://example.com:8080/a/") == "http://example.com:8080/a"


async def test_web_search_multi_merges_and_dedups(monkeypatch):
    async def fake_single(query, engine="duckduckgo", limit=10, timeout=15.0):
        if engine == "duckduckgo":
            return {
                "ok": True, "engine": engine, "query": query,
                "results": [
                    {"title": "A", "url": "https://example.com/a"},
                    {"title": "B", "url": "https://www.example.com/b/"},
                    {"title": "C", "url": "https://example.com/c"},
                ],
            }
        return {
            "ok": True, "engine": engine, "query": query,
            "results": [
                {"title": "B", "url": "https://example.com/b"},  # 与 DDG 重复（去尾斜杠）
                {"title": "D", "url": "https://example.com/d"},
            ],
        }

    monkeypatch.setattr("crawagent.harness.tools._web_search", fake_single)
    result = await _web_search_multi("kw", ["duckduckgo", "bing"], limit=10)

    assert result["ok"] is True
    urls = [r["url"] for r in result["results"]]
    # 去重后：A, B(来自 ddg，保留原始 www 前缀), C, D —— Bing 的 B 因归一化重复被跳过
    assert urls == ["https://example.com/a", "https://www.example.com/b/", "https://example.com/c", "https://example.com/d"]
    # 每条结果带来源引擎标记
    engines = {r["engine"] for r in result["results"]}
    assert "duckduckgo" in engines and "bing" in engines


async def test_web_search_multi_limit(monkeypatch):
    async def fake_single(query, engine="duckduckgo", limit=10, timeout=15.0):
        return {
            "ok": True, "engine": engine, "query": query,
            "results": [{"title": f"{engine}-{i}", "url": f"https://{engine}-{i}.com"} for i in range(limit)],
        }

    monkeypatch.setattr("crawagent.harness.tools._web_search", fake_single)
    result = await _web_search_multi("kw", ["duckduckgo", "bing"], limit=3)
    # 合并去重后按 limit 截断
    assert len(result["results"]) == 3


async def test_web_search_multi_partial_failure(monkeypatch):
    async def fake_single(query, engine="duckduckgo", limit=10, timeout=15.0):
        if engine == "duckduckgo":
            return {"ok": False, "engine": engine, "query": query, "error": "timeout", "results": []}
        return {
            "ok": True, "engine": engine, "query": query,
            "results": [{"title": "D", "url": "https://example.com/d"}],
        }

    monkeypatch.setattr("crawagent.harness.tools._web_search", fake_single)
    result = await _web_search_multi("kw", ["duckduckgo", "bing"], limit=10)

    # 一个引擎失败不影响整体成功
    assert result["ok"] is True
    assert result["failed"] == ["duckduckgo"]
    assert len(result["results"]) == 1


async def test_web_search_multi_all_failed(monkeypatch):
    async def fake_single(query, engine="duckduckgo", limit=10, timeout=15.0):
        return {"ok": False, "engine": engine, "query": query, "error": "boom", "results": []}

    monkeypatch.setattr("crawagent.harness.tools._web_search", fake_single)
    result = await _web_search_multi("kw", ["duckduckgo", "bing"], limit=10)

    assert result["ok"] is False
    assert result["results"] == []


async def test_search_executor_web_scope_success(tmp_path, monkeypatch):
    fake_results = [
        {"title": "示例标题", "url": "https://example.com/1", "snippet": "摘要一", "engine": "bing"},
        {"title": "示例标题2", "url": "https://example.com/2", "snippet": "", "engine": "bing"},
    ]

    async def fake_multi(query, engines, limit=10, timeout=15.0):
        return {"ok": True, "query": query, "engines": engines, "failed": [], "results": fake_results}

    monkeypatch.setattr("crawagent.harness.tools._web_search_multi", fake_multi)

    registry = create_default_tools(output_dir=str(tmp_path))
    executor = registry.get_executor("search")
    result = await executor({"query": "测试关键词", "scope": "web", "engines": ["duckduckgo", "bing"]})

    assert result["status_code"] == 200
    assert result["scope"] == "web"
    assert result["engines"] == ["duckduckgo", "bing"]
    assert len(result["results"]) == 2
    assert "互联网搜索" in result["content"]
    # 多引擎时结果行带来源标记
    assert "[bing]" in result["content"]


async def test_search_executor_web_scope_single_engine_compat(tmp_path, monkeypatch):
    """兼容旧调用：只传 engine 时等价于单引擎列表。"""
    captured = {}

    async def fake_multi(query, engines, limit=10, timeout=15.0):
        captured["engines"] = engines
        return {"ok": True, "query": query, "engines": engines, "failed": [], "results": []}

    monkeypatch.setattr("crawagent.harness.tools._web_search_multi", fake_multi)

    registry = create_default_tools(output_dir=str(tmp_path))
    executor = registry.get_executor("search")
    await executor({"query": "测试关键词", "scope": "web", "engine": "bing"})

    assert captured["engines"] == ["bing"]


async def test_search_executor_web_scope_failure(tmp_path, monkeypatch):
    async def fake_multi(query, engines, limit=10, timeout=15.0):
        return {"ok": False, "query": query, "engines": engines, "error": "timeout", "results": []}

    monkeypatch.setattr("crawagent.harness.tools._web_search_multi", fake_multi)

    registry = create_default_tools(output_dir=str(tmp_path))
    executor = registry.get_executor("search")
    result = await executor({"query": "测试关键词", "scope": "web", "engine": "bing"})

    assert result["status_code"] == 500
    assert result["results"] == []
    assert "timeout" in result["content"]
