"""纯函数测试：confidence 评分分支、历史裁剪中间件、列表页正则提取。

全部离线，不发网络请求（LLM 兜底路径不在本文件覆盖范围）。
"""
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# =============================================================
# confidence.evaluate_confidence 各扣/加分分支
# =============================================================


def test_confidence_empty_content():
    from crawagent.tools.confidence import evaluate_confidence
    r = evaluate_confidence(content="", title="")
    assert r.score == 30  # 100 - 50(empty) - 20(句子不足)
    assert r.should_upgrade is True
    assert any("content is empty" in x for x in r.reasons)


def test_confidence_short_content():
    from crawagent.tools.confidence import evaluate_confidence
    r = evaluate_confidence(content="x" * 50, title="")
    assert r.score == 50  # 100 - 30(短) - 20(句子不足)
    assert r.should_upgrade is True


def test_confidence_long_quality_clamps_to_100():
    from crawagent.tools.confidence import evaluate_confidence
    content = "这是一个足够长的中文句子，内容充实完整没有明显缺陷。" * 25  # ~600 字
    r = evaluate_confidence(content=content, title="标题")
    assert r.score == 100  # 长度+10 句子+10 中文比+10 → clamp
    assert r.should_upgrade is False


def test_confidence_pua_garble_severe():
    from crawagent.tools.confidence import evaluate_confidence
    content = "a" * 260 + "" * 40  # PUA 占比 13% > 10%
    r = evaluate_confidence(content=content, title="")
    assert r.score == 20  # 100 - 60(PUA) - 20(句子不足)
    assert r.should_upgrade is True
    assert any("PUA garble severe" in x for x in r.reasons)


def test_confidence_anti_crawl_marker():
    from crawagent.tools.confidence import evaluate_confidence
    r = evaluate_confidence(content="x" * 300, title="403 Forbidden 请登录")
    assert r.score == 30  # 100 - 50(反爬) - 20(句子不足)
    assert r.should_upgrade is True
    assert any("anti-crawl" in x for x in r.reasons)


def test_confidence_js_placeholder():
    from crawagent.tools.confidence import evaluate_confidence
    r = evaluate_confidence(content="y" * 300, title="请启用 JavaScript")
    assert r.score == 40  # 100 - 40(JS 占位) - 20(句子不足)
    assert r.should_upgrade is True
    assert any("JS placeholder" in x for x in r.reasons)


def test_confidence_marker_format():
    from crawagent.tools.confidence import evaluate_confidence
    low = evaluate_confidence(content="x" * 50, title="")
    assert low.format_marker().startswith("\n\n[LOW CONFIDENCE: score=50")
    high = evaluate_confidence(content="这是一个足够长的中文句子，内容充实完整没有明显缺陷。" * 25, title="标题")
    assert high.format_marker() == "\n\n[CONFIDENCE: score=100]"


# =============================================================
# TrimHistoryMiddleware：token 估算、ToolMessage 截断、滑动窗口
# =============================================================


def test_approx_token_count():
    from crawagent.graph.middleware import _approx_token_count
    assert _approx_token_count("") == 0
    assert _approx_token_count("中文测试") == 6  # 4 字 × 1.5
    assert _approx_token_count("abcdefgh") == 2  # 8 字符 / 4
    assert _approx_token_count("ab中文") == 3  # int(2*0.5 + 2*1.5)


def test_truncate_tool_message():
    from langchain_core.messages import ToolMessage
    from crawagent.graph.middleware import _truncate_tool_message

    long_msg = ToolMessage(content="知" * 1000, tool_call_id="t1", name="t")
    t = _truncate_tool_message(long_msg)
    assert len(t.content) < 1000
    assert t.content.startswith(long_msg.content[:250])  # 头保留
    assert t.content.endswith(long_msg.content[-125:])  # 尾保留
    assert "[truncated" in t.content
    assert t.tool_call_id == "t1" and t.id == long_msg.id

    short_msg = ToolMessage(content="短内容", tool_call_id="t2", name="t")
    assert _truncate_tool_message(short_msg) is short_msg  # 未超限原样返回


def _run_middleware(messages, mw):
    req = types.SimpleNamespace(messages=list(messages), state={})
    called = []
    mw.wrap_model_call(req, lambda r: called.append(r) or "ok")
    return req.messages, called


def _build_turns(n: int) -> list:
    from langchain_core.messages import HumanMessage, AIMessage
    msgs = []
    for i in range(1, n + 1):
        msgs.append(HumanMessage(content=f"第{i}轮用户提问内容" * 6))  # ~73 tokens
        msgs.append(AIMessage(content=f"第{i}轮智能回复内容" * 6))  # ~64 tokens
    return msgs


def test_window_keeps_recent_turns():
    from crawagent.graph.middleware import TrimHistoryMiddleware
    mw = TrimHistoryMiddleware(max_tokens=600, keep_recent_turns=3)
    messages, called = _run_middleware(_build_turns(5), mw)  # 5轮≈685 > 600
    assert called, "handler 必须被调用"
    assert len(messages) == 6  # 3 轮完整保留
    assert messages[0].content.startswith("第3轮")
    assert messages[-1].content.startswith("第5轮")


def test_eviction_half_watermark():
    """半水位淘汰：超限时一次性淘汰到 max_tokens/2，起点落在轮边界上。

    5 轮 ≈ 685 tokens > max_tokens=600 → target = 300。
    keep_recent_turns=1 → limit = 最后一轮边界，第 5 轮（≈137）已 ≤ 300，
    故淘汰到第 5 轮起点即可，不再多砍（摊销：下次需再涨 300 才触发）。
    """
    from crawagent.graph.middleware import TrimHistoryMiddleware
    mw = TrimHistoryMiddleware(max_tokens=600, keep_recent_turns=1)
    messages, _ = _run_middleware(_build_turns(5), mw)
    assert len(messages) == 2  # 只留第 5 轮（Human + AI）
    assert messages[0].content.startswith("第5轮")
    assert messages[0].__class__.__name__ == "HumanMessage"


def test_eviction_respects_min_turns_floor():
    """keep_recent_turns 是下限：N 轮本身超半水位时宁超预算，也不砍到少于 N 轮。

    与旧"激进裁剪到最后一轮"策略的关键差异 —— 保证当前任务上下文完整。
    """
    from crawagent.graph.middleware import TrimHistoryMiddleware
    mw = TrimHistoryMiddleware(max_tokens=200, keep_recent_turns=3)
    messages, _ = _run_middleware(_build_turns(5), mw)  # 3 轮 ≈411 > target 100
    assert len(messages) == 6  # 保底 3 轮，不激进砍到 1 轮
    assert messages[0].content.startswith("第3轮")
    assert messages[0].__class__.__name__ == "HumanMessage"


def test_window_empty_messages_passthrough():
    from crawagent.graph.middleware import TrimHistoryMiddleware
    mw = TrimHistoryMiddleware()
    messages, called = _run_middleware([], mw)
    assert messages == []
    assert called


def test_window_tool_message_truncated_in_loop():
    from langchain_core.messages import HumanMessage, ToolMessage
    from crawagent.graph.middleware import TrimHistoryMiddleware
    mw = TrimHistoryMiddleware(tool_result_max_chars=100)
    messages, _ = _run_middleware([HumanMessage("问题"), ToolMessage("长" * 500, tool_call_id="t1")], mw)
    assert len(messages[1].content) < 500
    assert "[truncated" in messages[1].content


# =============================================================
# list_extract 阶段1 正则：分组、导航过滤、去重、最少条数
# =============================================================


def test_stage1_regex_groups_and_filters():
    from crawagent.tools.list_extract_tool import _stage1_regex
    links = "".join(f'<a href="/article/{100 + i}.html">文章标题{i}</a>' for i in range(9))
    html = (
        f'<nav><a href="/login">登录</a><a href="/">首页</a></nav>'
        f'<div>{links}{links}</div>'  # 重复一遍 → 去重
        f'<a href="/page/2">下一页</a>'
    )
    items = _stage1_regex(html, base_url="https://example.com")
    assert len(items) == 9
    assert items[0]["title"] == "文章标题0"
    assert items[0]["url"] == "https://example.com/article/100.html"
    assert all("/article/" in it["url"] for it in items)
    assert not any("登录" in it["title"] or "首页" in it["title"] for it in items)


def test_stage1_regex_too_few_items():
    from crawagent.tools.list_extract_tool import _stage1_regex
    html = '<a href="/article/1.html">A</a><a href="/article/2.html">B</a>'
    assert _stage1_regex(html, base_url="https://example.com") == []


def test_normalize_url():
    from crawagent.tools.list_extract_tool import _normalize_url
    base = "https://example.com/"
    assert _normalize_url("javascript:void(0)", base) == ""
    assert _normalize_url("#section", base) == ""
    assert _normalize_url("mailto:a@b.c", base) == ""
    assert _normalize_url("/a/b.html#frag", base) == "https://example.com/a/b.html"
    assert _normalize_url("", base) == ""


def test_url_group_key():
    from crawagent.tools.list_extract_tool import _url_group_key
    assert _url_group_key("https://example.com/article/123.html") == "/article/"
    assert _url_group_key("https://example.com/article/456.html") == "/article/"
    assert _url_group_key("/article_45.html") == "/article.html"


def test_is_nav_footer_link():
    from crawagent.tools.list_extract_tool import _is_nav_footer_link
    assert _is_nav_footer_link("首页", "") is True
    assert _is_nav_footer_link("下一章", "/book/x.html") is True
    assert _is_nav_footer_link("", "https://x.com/login") is True
    assert _is_nav_footer_link("正文内容链接", "https://x.com/article/123.html") is False


# =============================================================
# 站点档案 + 站点级 Cookie（凭据属于站点，不属于全局设置）
# =============================================================


def test_site_profile_cookie_roundtrip(tmp_path, monkeypatch):
    from crawagent.tools import site_profile_tool
    from crawagent.tools.weread_tool import _weread_cookie

    # Phase 3: 重定向档案目录到临时 YAML 存储，跳过自动迁移
    monkeypatch.setattr(site_profile_tool, "_profiles_dir", lambda: tmp_path)
    monkeypatch.setattr(site_profile_tool, "_ensure_migrated", lambda: None)
    monkeypatch.setattr(site_profile_tool, "_MIGRATION_CHECKED", True)

    # 存档案 + Cookie → weread_tool 从档案读到
    site_profile_tool.upsert_site(
        "https://weread.qq.com",
        title="微信读书",
        strategy="weread_cookie",
        cookies="wr_vid=abc; wr_ssk=def",
    )
    assert _weread_cookie() == "wr_vid=abc; wr_ssk=def"

    # 未配置 Cookie 的站点返回空串
    assert site_profile_tool.get_site_cookies("https://example.com") == ""

    # list_site_profiles 只报状态不泄露 Cookie 原文
    out = site_profile_tool.list_site_profiles.invoke("weread.qq.com")
    assert "cookies: set" in out and "wr_vid" not in out

    # 传空串清除
    site_profile_tool.upsert_site("https://weread.qq.com", cookies="")
    assert _weread_cookie() == ""
    assert "cookies: not_set" in site_profile_tool.list_site_profiles.invoke("weread.qq.com")
