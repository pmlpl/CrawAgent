"""空最终回复兜底（变更 044）— 判定函数 + EmptyReplyRetryMiddleware 两级换路 + turn_engine 轮末提示。

背景：THINKING_DEPTH=high + 长上下文，思考 token 吃光中转侧输出配额，正文零字符被
finish_reason=length 截断，轮次正常 done 但用户一言未见（043 真机 2/2 长轮次命中）。
全部离线：handler 用桩脚本分发，thinking-off 模型用哨兵对象（不真调 get_llm）。
"""
import sys
import types
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from crawagent.graph.middleware import EmptyReplyRetryMiddleware, _is_empty_final_reply
from langchain.agents.middleware import ModelResponse


# ---------- 桩 ----------

class _Req:
    """最小 ModelRequest 桩：只承载 model + override。"""

    def __init__(self, model="orig-model"):
        self.model = model

    def override(self, **kw):
        r = _Req()
        r.__dict__.update({**self.__dict__, **kw})
        return r


def _empty_msg(fr="length"):
    return AIMessage(content="", response_metadata={"finish_reason": fr})


def _resp(*msgs):
    return ModelResponse(result=list(msgs))


def _script(*results):
    """handler 桩：按剧本返回结果，记录每次收到的 request.model。"""
    queue = list(results)
    calls = []

    def handler(req):
        calls.append(getattr(req, "model", None))
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    return handler, calls


# ---------- 判定函数 ----------

def test_detection():
    assert _is_empty_final_reply(AIMessage(content=""))
    assert _is_empty_final_reply(AIMessage(content="  \n\t"))
    # 多模态 content：文本块拼接判空
    assert _is_empty_final_reply(AIMessage(content=[{"type": "text", "text": ""}]))
    assert not _is_empty_final_reply(AIMessage(content=[{"type": "text", "text": "有货"}]))
    # 正文存在 → 非空
    assert not _is_empty_final_reply(AIMessage(content="正常回答"))
    # 带 tool_calls 的中间步永不触发
    assert not _is_empty_final_reply(AIMessage(
        content="", tool_calls=[{"name": "t", "args": {}, "id": "c1"}]))
    # 非 AIMessage / None 一律 False
    assert not _is_empty_final_reply(ToolMessage(content="", tool_call_id="c1"))
    assert not _is_empty_final_reply(HumanMessage(content=""))
    assert not _is_empty_final_reply(None)


# ---------- 重试阶梯 ----------

def test_ok_first_no_retry():
    """正常回复：handler 只调一次，原样返回（零干预）。"""
    mw = EmptyReplyRetryMiddleware()
    good = _resp(AIMessage(content="答案"))
    handler, calls = _script(good)
    assert mw.wrap_model_call(_Req(), handler) is good
    assert len(calls) == 1


def test_retry_once_verbatim_converges():
    """一级：空→原样重试一次→正常（瞬时抖动假设）。"""
    mw = EmptyReplyRetryMiddleware()
    empty, good = _resp(_empty_msg()), _resp(AIMessage(content="重试成功"))
    handler, calls = _script(empty, good)
    out = mw.wrap_model_call(_Req(), handler)
    assert len(calls) == 2
    assert out is good


def test_retry_twice_swaps_to_thinking_off_model():
    """二级：仍空→换 thinking-off 模型重试（request.override(model=...) 生效）。"""
    mw = EmptyReplyRetryMiddleware()
    sentinel = object()  # 哨兵：模拟 thinking-off 实例（不真调 get_llm）
    mw._retry_model = sentinel
    empty, good = _resp(_empty_msg()), _resp(AIMessage(content="换路成功"))
    handler, calls = _script(empty, empty, good)
    out = mw.wrap_model_call(_Req(), handler)
    assert len(calls) == 3
    assert calls[0] == calls[1] == "orig-model"  # 前两次原模型
    assert calls[2] is sentinel  # 第三次换 thinking-off
    assert out is good


def test_all_three_empty_returns_last():
    """两级都救不回：原样返回最后的结果，交给轮末兜底。"""
    mw = EmptyReplyRetryMiddleware()
    e1, e2, e3 = _resp(_empty_msg()), _resp(_empty_msg()), _resp(_empty_msg())
    handler, calls = _script(e1, e2, e3)
    out = mw.wrap_model_call(_Req(), handler)
    assert len(calls) == 3
    assert out is e3


def test_step1_retry_raises_returns_original():
    """一级重试抛异常：回退返回第一次的空结果，不向图循环泄漏。"""
    mw = EmptyReplyRetryMiddleware()
    empty = _resp(_empty_msg())
    handler, calls = _script(empty, RuntimeError("中转抽风"))
    out = mw.wrap_model_call(_Req(), handler)
    assert len(calls) == 2
    assert out is empty


def test_step2_retry_raises_returns_step1_result():
    """二级重试抛异常：回退返回一级的（仍空）结果。"""
    mw = EmptyReplyRetryMiddleware()
    e1, e2 = _resp(_empty_msg()), _resp(_empty_msg())
    handler, calls = _script(e1, e2, RuntimeError("模型构建失败"))
    out = mw.wrap_model_call(_Req(), handler)
    assert len(calls) == 3
    assert out is e2


def test_first_call_raises_propagates():
    """第一次调用就抛：异常照常上抛（保持现状语义，中间件不吞 LLM 错误）。"""
    mw = EmptyReplyRetryMiddleware()
    handler, calls = _script(RuntimeError("API key 无效"))
    with pytest.raises(RuntimeError):
        mw.wrap_model_call(_Req(), handler)
    assert len(calls) == 1


def test_bare_aimessage_result_also_handled():
    """handler 直接返回 AIMessage（非 ModelResponse 包装）同样走阶梯。"""
    mw = EmptyReplyRetryMiddleware()
    empty, good = _empty_msg(), AIMessage(content="裸消息回复")
    handler, calls = _script(empty, good)
    out = mw.wrap_model_call(_Req(), handler)
    assert len(calls) == 2
    assert out is good


def test_structured_output_toolmessage_tail_not_flagged():
    """结构化输出路径最后是 ToolMessage：不判空、不重试。"""
    mw = EmptyReplyRetryMiddleware()
    tool_tail = _resp(
        AIMessage(content="", tool_calls=[{"name": "x", "args": {}, "id": "c9"}]),
        ToolMessage(content='{"answer": 1}', tool_call_id="c9"),
    )
    handler, calls = _script(tool_tail)
    assert mw.wrap_model_call(_Req(), handler) is tool_tail
    assert len(calls) == 1


# ---------- turn_engine 轮末兜底 ----------

def _stub_agent(msgs):
    return types.SimpleNamespace(
        get_state=lambda cfg: types.SimpleNamespace(values={"messages": msgs}))


def test_warn_if_empty_final_emits_status():
    from crawagent.web.turn_engine import _warn_if_empty_final

    captured = []
    agent = _stub_agent([
        HumanMessage("任务"), _empty_msg(),
    ])
    assert _warn_if_empty_final(agent, {}, captured.append) is True
    assert len(captured) == 1
    ev = captured[0]
    assert ev["type"] == "status"
    assert "继续" in ev["line"] and "成果" in ev["line"]


def test_warn_if_empty_final_normal_turn_silent():
    from crawagent.web.turn_engine import _warn_if_empty_final

    captured = []
    agent = _stub_agent([HumanMessage("任务"), AIMessage(content="正常总结")])
    assert _warn_if_empty_final(agent, {}, captured.append) is False
    assert captured == []


def test_warn_if_empty_final_swallows_state_errors():
    from crawagent.web.turn_engine import _warn_if_empty_final

    def boom(cfg):
        raise RuntimeError("checkpoint 读取失败")
    captured = []
    assert _warn_if_empty_final(types.SimpleNamespace(get_state=boom), {}, captured.append) is False
    assert captured == []
