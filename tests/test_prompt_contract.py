"""契约测试：system.md 与代码的静态一致性（离线、零 LLM 成本，040 变更 3.5 节）。

防三类漂移：
1. 工具清单与 registry 脱节（新增工具忘了写进 system.md，或清单里留了已删工具）；
2. HARD 规则整段被误删（改提示词时动刀过宽）；
3. 工具描述写进依赖提示，教唆 Agent 预判拒绝调工具（013 实锤的教训固化为 lint）。
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

SYSTEM_MD = Path(__file__).resolve().parents[1] / "crawagent" / "prompts" / "system.md"

# system.md 必须存在的硬规则标记（§3.5 契约测试 #2）
HARD_RULE_MARKERS = [
    "RETRIEVE-FIRST",
    "ARCHIVE-ASK RULE",
    "ASK-USER RULE",
    "BATCH-FIRST RULE",
    "TOOL BUDGET RULE",
    "WORK FOLDER MISSING",
    "CROSS-SESSION ISOLATION",
    "ENVIRONMENT PREREQUISITE RULE",
    "TOOL-USE DECISION",
    "EXPERIENCE LOOP",
    "SELF-HEAL LADDER",
    "Step 0",
]


def _system_md() -> str:
    return SYSTEM_MD.read_text(encoding="utf-8")


def _registry_tools():
    from crawagent.tools.registry import build_all_tools
    return build_all_tools()


def test_tool_list_matches_registry():
    """system.md 数字清单与 build_all_tools() 双向相等 + 计数句与实数一致。"""
    md = _system_md()
    listed = set(re.findall(r"^\d+\.\s+(\w+)\(", md, re.M))
    actual = {t.name for t in _registry_tools()}

    missing = sorted(actual - listed)
    extra = sorted(listed - actual)
    assert not missing, f"registry 有但 system.md 工具清单缺: {missing}"
    assert not extra, f"system.md 工具清单有但 registry 无: {extra}"

    m = re.search(r"You have (\d+) powerful tools", md)
    assert m, "TOOL-USE DECISION 段的 'You have N powerful tools' 计数句缺失"
    assert int(m.group(1)) == len(actual), (
        f"计数句说 {m.group(1)} 个工具，registry 实际 {len(actual)} 个"
    )


def test_hard_rules_present():
    """关键 HARD 规则标记存在——整段误删当场红。"""
    md = _system_md()
    gone = [m for m in HARD_RULE_MARKERS if m not in md]
    assert not gone, f"system.md 缺硬规则标记: {gone}"


def test_tool_doc_no_prereq_hint():
    """工具描述禁写依赖提示（013：写了 Agent 就预判拒绝不调工具）。"""
    bad = re.compile(
        r"requires?\s+\w+\s+(to\s+be\s+)?installed|need(?:s)?\s+\w+\s+installed"
        r"|需要先安装|需要安装",
        re.I,
    )
    offenders = [
        t.name
        for t in _registry_tools()
        if t.description and bad.search(t.description)
    ]
    assert not offenders, f"工具描述含依赖提示（013 教训）: {offenders}"
