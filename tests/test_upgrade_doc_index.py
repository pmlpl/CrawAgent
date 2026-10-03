"""升级改动文档索引契约测试（upgrade-doc skill「目录与分类」自动上锁产物）。

守五件事：每份规格已登记 / 目录链接可达 / 分类合法且唯一 /
文档 meta 状态与目录一致 / 快照计数对账。
漂移（漏登记、改名不回写、状态行停更、快照数字不对）在这里直接红。

本项目实例：2026-10-02 由 upgrade-doc skill「自动上锁」规则落装
（条件①规格 40 份 ≥ 20，条件②当日发现 018-021/023 状态行漂移）。
"""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DOC_DIR = REPO_ROOT / "升级改动文档"
INDEX_PATH = DOC_DIR / "000-目录.md"

CATEGORIES = [
    "Agent 行为与提示词",
    "MCP 生态",
    "工具与爬取能力",
    "会话与产物",
    "前端与交互",
    "模型与运行时",
    "架构与技术债",
    "质量与测试",
    "依赖升级",
]


def _spec_files():
    return sorted(p for p in DOC_DIR.glob("[0-9][0-9][0-9]-*.md") if p.name != "000-目录.md")


def _normalize_status(text):
    text = text.strip().strip("*").strip()
    if text.startswith(("已实施", "已完成")):
        return "已实施"
    if text.startswith(("待批准", "待实施")):
        return "待实施"
    if text.startswith("已阻塞"):
        return "已阻塞"
    if text.startswith("已撤档"):
        return "已撤档"
    return text


def _index_rows():
    """返回 {分类: [(编号, 状态), ...]}，编号形如 '004'。"""
    sections = {}
    current = None
    for line in INDEX_PATH.read_text(encoding="utf-8").splitlines():
        header = re.match(r"^## (.+?)\s*$", line)
        if header:
            current = header.group(1).strip()
            sections.setdefault(current, [])
            continue
        if current is None:
            continue
        row = re.match(
            r"^\|\s*\[(\d{3})\]\([^)]+\)\s*\|[^|]*\|([^|]*)\|", line
        )
        if row:
            sections[current].append((row.group(1), row.group(2)))
    return sections


def test_index_exists():
    assert INDEX_PATH.exists(), "000-目录.md 不存在——upgrade-doc 规定规格目录必须有总索引"


def test_every_spec_registered():
    registered = {num for rows in _index_rows().values() for num, _ in rows}
    specs = {p.name.split("-")[0] for p in _spec_files()}
    missing = specs - registered
    stale = registered - specs
    assert not missing, f"漏登记（文档在、目录无行）：{sorted(missing)}"
    assert not stale, f"目录悬挂（目录有行、文件不在，疑似改名/撤档未回写）：{sorted(stale)}"


def test_index_links_resolve():
    links = re.findall(r"\[\d{3}\]\(([^)]+)\)", INDEX_PATH.read_text(encoding="utf-8"))
    broken = [link for link in links if not (DOC_DIR / link).exists()]
    assert not broken, f"目录链接指向不存在的文件（疑似文档改名未回写）：{broken}"


def test_categories_legal_and_unique():
    sections = _index_rows()
    unknown = {cat for cat, rows in sections.items() if rows and cat not in CATEGORIES}
    assert not unknown, (
        f"目录出现了分类定义之外的分类（新分类须先登记分类定义并同步测试常量）：{sorted(unknown)}"
    )
    seen = {}
    for cat, rows in sections.items():
        for num, _ in rows:
            seen.setdefault(num, []).append(cat)
    dup = {num: cats for num, cats in seen.items() if len(cats) > 1}
    assert not dup, f"一份规格登记进多个分类（只许单一主分类）：{dup}"


def test_doc_meta_consistent_with_index():
    """文档 meta 表的「分类/状态」行若存在，必须与目录行一致（旧文档无该行则跳过）。"""
    num2status = {num: _normalize_status(st) for rows in _index_rows().values() for num, st in rows}
    for p in _spec_files():
        num = p.name.split("-")[0]
        text = p.read_text(encoding="utf-8")
        m_status = re.search(r"^\|\s*状态\s*\|([^|]+)\|", text, re.M)
        if m_status and num in num2status:
            assert _normalize_status(m_status.group(1)) == num2status[num], (
                f"{p.name} meta 状态「{m_status.group(1).strip()}」与目录行「{num2status[num]}」不一致"
            )
        m_cat = re.search(r"^\|\s*分类\s*\|([^|]+)\|", text, re.M)
        if m_cat:
            owner = [
                cat
                for cat, rows in _index_rows().items()
                if any(n == num for n, _ in rows)
            ]
            assert owner and owner[0] == m_cat.group(1).strip(), (
                f"{p.name} meta 分类「{m_cat.group(1).strip()}」与目录归属「{owner}」不一致"
            )


def test_snapshot_counts_match():
    m = re.search(
        r"快照[^：]*：\s*(\d+)\s*份（已实施\s*(\d+).*?待实施\s*(\d+).*?已阻塞\s*(\d+)",
        INDEX_PATH.read_text(encoding="utf-8"),
    )
    assert m, "目录头部缺快照行（快照 <日期>：<总数> 份（已实施 X · 待实施 Y · 已阻塞 Z））"
    total, done, pending, blocked = (int(x) for x in m.groups())
    rows = [(num, st) for rs in _index_rows().values() for num, st in rs]
    counts = {"已实施": 0, "待实施": 0, "已阻塞": 0, "已撤档": 0}
    for _, st in rows:
        counts[_normalize_status(st)] += 1
    assert len(rows) == total, f"快照总数 {total} ≠ 目录行数 {len(rows)}"
    assert (counts["已实施"], counts["待实施"], counts["已阻塞"]) == (done, pending, blocked), (
        f"快照计数（已实施 {done}/待实施 {pending}/已阻塞 {blocked}）与目录行实际分布 {counts} 不符"
    )
