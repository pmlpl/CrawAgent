"""ScriptForcerMiddleware 状态机测试 — 全部离线，不调 LLM。

覆盖 7 个触发点（见 handoff §1.3）：
    1. failure_threshold=3 → 3 次失败后下一次 wrap_model_call 注入强制脚本指令
    2. 第 5 次失败 → _force_script_hard（禁止预制工具，只有脚本）
    3. max_total_calls → 超限后注入强制收尾（FORCE SUMMARY）
    4. max_consecutive_same → 同一工具连续调用超限注入告警
    5. max_run_custom_script → 脚本次数用完后注入升级指令
    6. script_stall_threshold → 脚本正文长度连续停滞触发 escalate
    7. 付费墙 / SLIDER CAPTCHA命中不算失败，不累积 _consecutive_failures
"""
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage

from crawagent.graph.script_forcer import ScriptForcerMiddleware


def _tool_request(name: str = "crawl_webpage", args: dict | None = None):
    # args 默认为空 dict：不带 url/code，避免触发"开拓者护栏"的 URL 跟踪
    # （同一 URL ≥4 次尝试 + body<4000 会注入探索提醒，在 elif 链中优先级最高，遮蔽被测分支）
    return SimpleNamespace(tool_call=SimpleNamespace(name=name, args=args if args is not None else {}))


def _tool_result(content: str) -> ToolMessage:
    return ToolMessage(content=content, tool_call_id="test-call-1")


def _call(mf: ScriptForcerMiddleware, name: str, content: str, args: dict | None = None):
    """驱动一次 wrap_tool_call：handler 原样返回构造的 ToolMessage。"""
    req = _tool_request(name, args)
    return mf.wrap_tool_call(req, lambda r: _tool_result(content))


FAILURE = "ERROR: site blocked us with anti-crawl protection, cannot fetch this page at all this time"
SUCCESS = 'SAVED: {"url": "https://example.com/page", "title": "ok"} CONFIDENCE: 0.92 — extraction succeeded fine'
PAYWALL = "this chapter is vip-locked on the source site; vip account may be required to read the full content"
CAPTCHA = "geetest slider captcha detected: 滑动验证 required before the page content can be viewed"


def test_three_failures_inject_force_script_via_wrap_model_call():
    """触发点 1：3 次失败 → 下一次 wrap_model_call 注入 run_custom_script 强制指令。"""
    mf = ScriptForcerMiddleware(failure_threshold=3, hard_limit=5)
    for _ in range(3):
        _call(mf, "crawl_webpage", FAILURE)
    assert mf._consecutive_failures == 3
    assert mf._force_script_next is True

    # 通过 wrap_model_call 验证注入（缓存安全：追加到末尾，不动前缀）
    captured = {}
    def handler(request):
        captured["messages"] = request.messages
        return "ok"
    req = SimpleNamespace(messages=[HumanMessage(content="hi")])
    mf.wrap_model_call(req, handler)
    injected = [m for m in captured["messages"] if isinstance(m, SystemMessage)]
    assert len(injected) == 1
    assert "run_custom_script" in injected[0].content
    # 注入后一次性标志被消费
    assert mf._force_script_next is False


def test_five_failures_force_hard():
    """触发点 2：第 5 次失败 → _force_script_hard（禁用全部预制工具）。"""
    mf = ScriptForcerMiddleware(failure_threshold=3, hard_limit=5)
    for _ in range(5):
        _call(mf, "browse_and_crawl", FAILURE)
    assert mf._force_script_hard is True
    msg = mf._build_force_message()
    assert msg is not None
    assert "BUILT-IN TOOLS VOIDED" in msg.content


def test_max_total_calls_force_summary():
    """触发点 3：max_total_calls=30 → 30 次后注入强制收尾（FORCE SUMMARY: STOP）。"""
    mf = ScriptForcerMiddleware(max_total_calls=30)
    for i in range(30):
        # 交替工具名：避免连带触发 max_consecutive_same（默认 4）干扰"消费后不再注入"断言
        _call(mf, "crawl_webpage" if i % 2 == 0 else "browse_and_crawl", SUCCESS)
    assert mf._force_summary is True
    msg = mf._build_force_message()
    assert "FORCED WRAP-UP" in msg.content
    assert "30" in msg.content
    # 消费后不再注入
    assert mf._build_force_message() is None


def test_consecutive_same_tool_warning():
    """触发点 4：同一工具连调 5 次 → 注入同工具循环告警。"""
    mf = ScriptForcerMiddleware(max_consecutive_same=5)
    for _ in range(5):
        _call(mf, "extract_content", SUCCESS)
    assert mf._consecutive_same_count == 5
    msg = mf._build_force_message()
    assert msg is not None
    assert "SAME-TOOL LOOP" in msg.content
    assert "extract_content" in msg.content


def test_run_custom_script_hard_limit():
    """触发点 5：max_run_custom_script=4 → 4 次脚本后注入升级指令（禁 requests 直连）。"""
    mf = ScriptForcerMiddleware(max_run_custom_script=4)
    for _ in range(4):
        _call(mf, "run_custom_script", "BODY LEN: 5000\n...extracted content is large and healthy...")
    assert mf._run_custom_script_count == 4
    assert mf._force_script_escalate is True
    msg = mf._build_force_message()
    assert "SCRIPT LADDER ESCALATION" in msg.content
    assert "FORBIDDEN" in msg.content
    assert "HARD CAP" in msg.content


def test_script_stall_escalation():
    """触发点 6：正文增长 <10% 连续达阈值（3 次）→ escalate。"""
    mf = ScriptForcerMiddleware(script_stall_threshold=3)
    lens = [1000, 1050, 1020, 1005]  # 第 2/3/4 次连续 3 次停滞
    for n in lens:
        _call(mf, "run_custom_script", f"BODY LEN: {n}\ncontent...")
    assert mf._script_stall_count == 3
    assert mf._force_script_escalate is True
    msg = mf._build_force_message()
    assert "SCRIPT LADDER ESCALATION" in msg.content


def test_paywall_and_captcha_not_failure():
    """触发点 7：付费墙 / 滑块命中不算失败，不累积 _consecutive_failures。

    注意：滑块验证码的自动注入（_force_captcha_alert）已于 2026-09 上层决策取消，
    让 Agent 自由尝试各种手段组合，不再有监督员强制打断。
    见 script_forcer.py wrap_tool_call 中 captcha 处理段注释。
    """
    mf = ScriptForcerMiddleware()
    # 付费墙：不计数、不触发强制脚本
    _call(mf, "crawl_webpage", PAYWALL)
    assert mf._consecutive_failures == 0
    assert mf._force_script_next is False

    # 滑块：当前设计——不计数、也不自动注入 captcha alert，让 Agent 自由发挥
    _call(mf, "browse_and_crawl", CAPTCHA)
    assert mf._consecutive_failures == 0
    assert mf._force_captcha_alert is False  # 设计决策：取消自动注入
    assert mf._build_force_message() is None  # captcha 不产生任何 force message

    # 成功结果清零失败计数（对照）
    _call(mf, "crawl_webpage", FAILURE)
    _call(mf, "crawl_webpage", SUCCESS)
    assert mf._consecutive_failures == 0


def test_public_reset():
    """§2.2：公开 reset() 方法清空全部状态，跨模块不再穿透私有 _reset。"""
    mf = ScriptForcerMiddleware(failure_threshold=3)
    for _ in range(3):
        _call(mf, "crawl_webpage", FAILURE)
    assert mf._consecutive_failures == 3
    assert hasattr(mf, "reset") and callable(mf.reset)
    mf.reset()
    assert mf._consecutive_failures == 0
    assert mf._total_tool_calls == 0
    assert mf._force_script_next is False
    assert mf._build_force_message() is None
