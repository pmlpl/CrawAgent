"""水位淘汰中间件自测：淘汰行为 + 前缀稳定性"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
from langchain_core.messages import HumanMessage, AIMessage, ToolMessage
from crawagent.graph.middleware import TrimHistoryMiddleware, _msg_token_count


class FakeRequest:
    def __init__(self, messages):
        self.messages = messages


def make_history(n_turns, text_len=3000):
    msgs = []
    for t in range(n_turns):
        msgs.append(HumanMessage(content=f"turn{t}: " + "x" * text_len))
        msgs.append(AIMessage(content="", tool_calls=[{"id": f"c{t}", "name": "tool", "args": {}}]))
        msgs.append(ToolMessage(content="r" * text_len, tool_call_id=f"c{t}"))
        msgs.append(AIMessage(content=f"answer{t}: " + "y" * 200))
    return msgs


mw = TrimHistoryMiddleware(max_tokens=4000, keep_recent_turns=3, tool_result_max_chars=2000)

# 场景1：未超限 → 不滑动窗口（仅 ToolMessage 被确定性截断，Human/AI 原样保留）
msgs = make_history(2)
r = FakeRequest(msgs)
mw.wrap_model_call(r, lambda req: req)
assert len(r.messages) == len(msgs), "场景1失败: 消息数量变了"
assert r.messages[0] == msgs[0] and r.messages[-1] == msgs[-1], "场景1失败: Human/AI 被改动"
print("场景1 PASS: 未超限不滑动窗口")

# 场景2：超限 → 淘汰到半水位，保留至少最近 3 轮
msgs = make_history(10)
total = sum(_msg_token_count(m) for m in msgs)
r = FakeRequest(msgs)
mw.wrap_model_call(r, lambda req: req)
kept = r.messages
kept_total = sum(_msg_token_count(m) for m in kept)
n_humans = sum(1 for m in kept if isinstance(m, HumanMessage))
assert kept_total <= 4000, f"场景2失败: kept={kept_total}"
assert n_humans >= 3, f"场景2失败: 只保留{n_humans}轮"
assert kept[-1].content.startswith("answer9"), "场景2失败: 当前轮回复被裁掉"
print(f"场景2 PASS: {total}->{kept_total} tokens, 保留{n_humans}轮")

# 场景3（缓存核心）：淘汰后继续追加，只要不再次超限，前缀与上次淘汰后完全一致
prev = kept
msgs2 = prev + [HumanMessage(content="turn10: " + "x" * 20), AIMessage(content="ok")]
r2 = FakeRequest(msgs2)
assert sum(_msg_token_count(m) for m in msgs2) <= 4000, "测试准备失败: 应不超限"
mw.wrap_model_call(r2, lambda req: req)
assert r2.messages == msgs2, "场景3失败: 未超限时被错误裁剪"
assert r2.messages[:len(prev)] == prev, "场景3失败: 未淘汰时前缀变了"
print("场景3 PASS: 未再超限时前缀稳定（追加不移动窗口起点）→ 缓存可持续命中")

# 场景4：继续增长直到再次超限 → 再次淘汰到半水位
big = prev + make_history(6, text_len=3000)
r3 = FakeRequest(big)
mw.wrap_model_call(r3, lambda req: req)
kept3_total = sum(_msg_token_count(m) for m in r3.messages)
assert kept3_total <= 4000, f"场景4失败: kept={kept3_total}"
print(f"场景4 PASS: 再次超限→再次淘汰到 {kept3_total} tokens")

print("ALL PASS")
