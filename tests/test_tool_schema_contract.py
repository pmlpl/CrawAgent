"""工具 schema 契约测试 — langchain 保留字改名回归（2026-10-04 真机发现）。

langchain-core 生成 args_schema 时会把撞保留字的参数改名（实测：args → v__args，
langchain-core 1.6.4）。LLM 按 schema 传改名后的字段，func() 收到陌生 kwarg 直接
TypeError。单测直调 .func() 绕过 langchain 封装，此 bug 全绿不现形——add_mcp_server
就是这样坏了一段时间没人发现。

本测试锁死：每个注册工具的函数签名参数名必须与 args_schema 字段名完全一致，
任何 langchain 改名/漂移在测试期就红。
"""
import inspect

from crawagent.tools.registry import build_all_tools


def test_registry_tool_schema_fields_match_signature():
    tools = build_all_tools()
    assert tools, "registry 不应为空"
    bad = []
    for t in tools:
        fn = t.func if t.func is not None else t.coroutine
        if fn is None:
            bad.append(f"{t.name}: 无 func/coroutine 可比对")
            continue
        sig_params = set(inspect.signature(fn).parameters)
        fields = getattr(t.args_schema, "model_fields", None) or {}
        schema_fields = set(fields)
        if sig_params != schema_fields:
            bad.append(
                f"{t.name}: 签名 {sorted(sig_params)} != schema {sorted(schema_fields)}"
            )
    assert not bad, (
        "工具签名与 args_schema 不一致（langchain 保留字改名类 bug）：\n" + "\n".join(bad)
    )
