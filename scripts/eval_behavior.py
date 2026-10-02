"""040 Agent 行为评测集 — 进程内直驱真 Agent，工具换桩，逐例判定。

用法（改 system.md / 换模型前后各跑一遍当回归门）：
  uv run python scripts/eval_behavior.py                      # 全量黄金集
  uv run python scripts/eval_behavior.py --filter spa_shell   # 单跑一例
  uv run python scripts/eval_behavior.py --model <model_id> --out run2.json

机制：
- 接管 agent._build_tools()：基础工具 + check_mcp_status/wait_capture_ready，
  不装原生 MCP 工具（评测要「MCP 未运行」的确定性环境，也避免真连 MCP）。
- 工具执行入口（StructuredTool.func）全部换成桩分发器：保留
  name/description/args_schema 供 LLM 绑定，剧本返回 canned 输出并记录调用流水。
- SYSTEM_PROMPT 在 agent 模块 import 时读盘，评测每次新起进程，
  注入坏规则后重跑自然生效。
- 判定全确定性词表（无 LLM-as-judge），保证分数可复现。
"""
import argparse
import json
import re
import sys
import threading
import time
from dataclasses import replace
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from eval_cases import CASES, DEFAULT_STUBS  # noqa: E402

SYSTEM_MD = ROOT / "crawagent" / "prompts" / "system.md"

_TOOLBOX: dict = {}


class StubBook:
    """按案例剧本分发工具调用并记录 (工具名, 参数) 流水。"""

    def __init__(self, case):
        self.script = {k: list(v) for k, v in (case.script or {}).items()}
        self.ask = list(case.ask or [])
        self.calls = []

    def respond(self, name: str, args: dict) -> str:
        self.calls.append((name, dict(args)))
        if name == "ask_user":
            return self.ask.pop(0) if self.ask else "不用"
        seq = self.script.get(name)
        if not seq:
            return DEFAULT_STUBS.get(name, "OK（eval 通用桩，本案例未编排该工具）")
        return seq.pop(0) if len(seq) > 1 else seq[0]


def _install_toolbox():
    """接管 agent._build_tools：基础工具 + check_mcp_status/wait_capture_ready。"""
    import crawagent.graph.agent as agent_mod
    from crawagent.tools.mcp_capture_tool import check_mcp_status, wait_capture_ready
    from crawagent.tools.registry import build_all_tools

    def _patched_build_tools():
        tools = [*build_all_tools(), check_mcp_status, wait_capture_ready]
        _TOOLBOX["tools"] = tools
        return tools

    agent_mod._build_tools = _patched_build_tools
    return agent_mod


def _apply_stubs(book: StubBook) -> None:
    """把案例桩装到工具实例上（func 换桩、coroutine 清空防真实协程旁路）。"""
    for t in _TOOLBOX["tools"]:
        name = t.name

        def _mk(n):
            def _stub(**kwargs):
                return book.respond(n, kwargs)
            return _stub

        t.func = _mk(name)
        t.coroutine = None
        if not callable(t.func) or t.coroutine is not None:
            raise RuntimeError(f"工具 {name} 桩安装失败，评测禁止带真工具运行")


def _content_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            b.get("text", "") if isinstance(b, dict) else str(b) for b in content
        )
    return str(content)


def _is_subseq(needle: list, hay: list) -> bool:
    i = 0
    for h in hay:
        if i < len(needle) and h == needle[i]:
            i += 1
    return i == len(needle)


def judge(case, calls: list, reply: str) -> list:
    """确定性判定：返回失败理由列表（空 = PASS）。"""
    reasons = []
    names = [n for n, _ in calls]

    if case.expect_seq and not _is_subseq(case.expect_seq, names):
        reasons.append(
            f"期望工具序列 {case.expect_seq} 未按序出现，实际调用: {names or '无'}"
        )
    if case.forbid:
        hit = [n for n in case.forbid if n in names]
        if hit:
            reasons.append(f"禁用工具被调用: {hit}，全部调用: {names}")
    for tool, spec in (case.count or {}).items():
        actual = names.count(tool)
        for op, want in spec.items():
            ok = {"==": actual == want, "<=": actual <= want, ">=": actual >= want}.get(op)
            if ok is None:
                reasons.append(f"未知比较符 {op!r}")
            elif not ok:
                reasons.append(f"{tool} 调用次数期望 {op}{want}，实际 {actual}")
    if case.total_max is not None and len(names) > case.total_max:
        reasons.append(f"总工具调用 {len(names)} 超上限 {case.total_max}: {names}")
    for tool, key, sub in (case.arg_contains or []):
        found = any(
            n == tool and sub in str((a or {}).get(key, "")) for n, a in calls
        )
        if not found:
            reasons.append(f"{tool} 的 {key} 参数没有任何一次包含「{sub}」")
    if case.reply_re and not re.search(case.reply_re, reply or ""):
        reasons.append(
            f"回复未匹配 /{case.reply_re}/，回复开头: {(reply or '')[:120]!r}"
        )
    if case.check:
        custom = case.check(calls, reply)
        if custom:
            reasons.append(f"自定义断言失败: {custom}")
    return reasons


def _fmt_calls(calls: list) -> str:
    return " → ".join(n for n, _ in calls) if calls else "（无工具调用）"


def run_case(agent, case):
    """跑单例：同步 invoke（与生产 turn_engine 同路径；项目中间件只有同步
    wrap_model_call，ainvoke 会 NotImplementedError），用 daemon 线程做超时。"""
    from langchain_core.messages import HumanMessage

    book = StubBook(case)
    _apply_stubs(book)
    agent.reset_turn_state()

    t0 = time.time()
    container: dict = {}

    def _worker():
        try:
            container["state"] = agent.invoke(
                {"messages": [HumanMessage(content=case.user)]}
            )
        except Exception as e:  # LLM 断连 / 框架异常都不中断整场
            container["error"] = e

    th = threading.Thread(target=_worker, daemon=True)
    th.start()
    th.join(case.timeout)
    elapsed = round(time.time() - t0, 1)

    def _fail(reasons):
        return {
            "id": case.id, "rule": case.rule, "status": "FAIL",
            "failures": reasons, "calls": book.calls, "reply": "",
            "elapsed": elapsed,
        }

    if th.is_alive():
        return _fail([f"超时（>{case.timeout}s）"])
    if "error" in container:
        e = container["error"]
        return _fail([f"运行异常 {type(e).__name__}: {e}"])

    state = container.get("state") or {}
    reply = ""
    for m in reversed(state.get("messages", [])):
        if type(m).__name__ == "AIMessage" and not getattr(m, "tool_calls", None):
            reply = _content_text(m.content)
            break

    failures = judge(case, book.calls, reply)
    return {
        "id": case.id, "rule": case.rule,
        "status": "PASS" if not failures else "FAIL",
        "failures": failures, "calls": book.calls, "reply": reply,
        "elapsed": elapsed,
    }


def _model_label(args_model) -> str:
    if args_model:
        return args_model
    # get_llm(None) 的真实解析结果（模型池四档），settings.default_model 只是
    # 落盘字段、不等于实际生效模型（033 池选后两者可能不同）
    from crawagent.llm.model import resolve_model
    try:
        return resolve_model()[0]
    except Exception:
        from crawagent.config.settings import get_settings
        return get_settings().default_model


def main():
    ap = argparse.ArgumentParser(description="CrawAgent Agent 行为评测（变更 040）")
    ap.add_argument("--filter", default=None, help="只跑 id 含该子串的案例")
    ap.add_argument("--model", default=None, help="换模型对比（传模型 ID）")
    ap.add_argument("--out", default=None, help="JSON 报告输出路径（供改前/改后对比）")
    ap.add_argument("--retries", type=int, default=0,
                    help="失败例自动重跑次数（重跑通过标 FLAKY 不计通过）")
    ap.add_argument("--timeout", type=int, default=None, help="单例超时秒数覆盖")
    args = ap.parse_args()

    cases = [c for c in CASES if not args.filter or args.filter in c.id]
    if not cases:
        print(f"没有匹配 --filter={args.filter!r} 的案例")
        sys.exit(2)

    # 预检：LLM 配置不可用直接人话退出，不出半份报告
    try:
        agent_mod = _install_toolbox()
        agent = agent_mod.get_agent(model=args.model)
    except Exception as e:
        print(f"Agent/LLM 初始化失败，评测未运行：{type(e).__name__}: {e}")
        print("请检查 .env 的 LLM 配置（或 --model 参数）后重试。")
        sys.exit(2)
    if not _TOOLBOX.get("tools"):
        print("工具箱为空，评测未运行（_build_tools 接管失败）。")
        sys.exit(2)

    label = _model_label(args.model)
    md_mtime = datetime.fromtimestamp(SYSTEM_MD.stat().st_mtime).isoformat(timespec="seconds")
    print("=" * 64)
    print(f"CrawAgent 行为评测（040）  模型: {label}  system.md mtime: {md_mtime}")
    print(f"案例数: {len(cases)}  超时: {args.timeout or '各例默认 180s'}")
    print("=" * 64)

    results = []
    for idx, case in enumerate(cases, 1):
        if args.timeout:
            case = replace(case, timeout=args.timeout)
        res, attempt, flaky = None, 1, False
        for attempt in range(1, max(0, args.retries) + 2):
            res = run_case(agent, case)
            if res["status"] == "PASS":
                flaky = attempt > 1
                break
        res["status"] = "FLAKY" if flaky else res["status"]
        results.append(res)

        mark = {"PASS": "PASS", "FLAKY": "FLAKY", "FAIL": "FAIL"}[res["status"]]
        print(f"[{idx:02d}/{len(cases)}] {res['id']:<28} {mark:<5} "
              f"{res['elapsed']:>6.1f}s  {_fmt_calls(res['calls'])}")
        print(f"    规则: {res['rule']}")
        for r in res["failures"]:
            print(f"    FAIL: {r}")

    passed = sum(1 for r in results if r["status"] == "PASS")
    flaky = sum(1 for r in results if r["status"] == "FLAKY")
    total = len(results)
    print("-" * 64)
    print(f"通过率: {passed}/{total} ({passed / total * 100:.1f}%)"
          + (f"   FLAKY(不计通过): {flaky}" if flaky else ""))

    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "model": label,
        "system_md_mtime": md_mtime,
        "passed": passed, "flaky": flaky, "total": total,
        "pass_rate": round(passed / total, 4),
        "cases": [
            {
                "id": r["id"], "status": r["status"], "rule": r["rule"],
                "failures": r["failures"],
                "calls": [
                    {"name": n, "args": {
                        k: (str(v)[:200] + "…") if len(str(v)) > 200 else str(v)
                        for k, v in (a or {}).items()}}
                    for n, a in r["calls"]
                ],
                "reply_head": r["reply"][:300],
                "elapsed": r["elapsed"],
            }
            for r in results
        ],
    }
    if args.out:
        Path(args.out).write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"JSON 报告已写入: {args.out}")
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
