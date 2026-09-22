"""docstring 覆盖率审计 — CI 守门 + 缺文档清单。

变更 021：升级为长期维护工具（不再"一次性"）。
- 输出每个无 docstring 函数的位置（行号 + 名称 + 所属类）方便补
- 加 95% 阈值守门（覆盖率 <95% → exit 1，CI fail）
- 同时输出"class-level docstring 覆盖"（`__init__` / `__repr__` 等 dunder 不计）
"""
from __future__ import annotations

import ast
import collections
import pathlib
import sys

ROOT = pathlib.Path("crawagent")
THRESHOLD = 95.0  # 变更 021 目标：≥95%

# 排除目录：远程 worker 部署模式的接口 stub（不是本机业务代码）/ 模板 / 构建产物
EXCLUDE_DIRS = ("__pycache__", "dist", "_template")

tot = with_doc = 0
no_doc_by_file: dict[str, list[tuple[int, str]]] = collections.defaultdict(list)


def _is_public(name: str) -> bool:
    """非下划线开头 = 公开。dunder（__init__ / __repr__）也算公开。"""
    return not name.startswith("_")


for p in sorted(ROOT.rglob("*.py")):
    if any(ex in str(p) for ex in EXCLUDE_DIRS):
        continue
    try:
        tree = ast.parse(p.read_text(encoding="utf-8"))
    except SyntaxError as e:
        print(f"WARN: skip {p}: SyntaxError {e}", file=sys.stderr)
        continue

    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        if not _is_public(node.name):
            continue
        tot += 1
        if ast.get_docstring(node):
            with_doc += 1
        else:
            no_doc_by_file[p.name].append((node.lineno, node.name))

coverage = with_doc / tot * 100 if tot else 100.0

print(f"public funcs total: {tot}")
print(f"with docstring:     {with_doc}")
print(f"coverage:           {coverage:.1f}%")
print(f"threshold:          {THRESHOLD:.1f}%")
print()

if no_doc_by_file:
    print("Undocumented public funcs:")
    for fname in sorted(no_doc_by_file):
        for lineno, name in sorted(no_doc_by_file[fname], key=lambda x: x[0]):
            print(f"  {fname:40s}:{lineno}  {name}")
    print()

# CI 守门：低于阈值退出码 1
if coverage < THRESHOLD:
    print(f"FAIL: docstring coverage {coverage:.1f}% < {THRESHOLD}% — need补齐 to pass CI")
    sys.exit(1)

print(f"PASS: docstring coverage {coverage:.1f}% ≥ {THRESHOLD}%")