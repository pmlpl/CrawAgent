"""一次性脚本 — 找长 public 函数（> 40 行），P4 #14 候选清单。"""
import ast, pathlib

for p in sorted(pathlib.Path("crawagent").rglob("*.py")):
    s = str(p)
    if "__pycache__" in s or "dist" in s or "_template" in s:
        continue
    try:
        tree = ast.parse(p.read_text(encoding="utf-8"))
    except Exception:
        continue
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and not node.name.startswith("_"):
            end = getattr(node, "end_lineno", node.lineno)
            lines = end - node.lineno + 1
            if lines > 40:
                rel = p.relative_to("crawagent").as_posix()
                print(f"{rel}:{node.lineno}  {node.name}  ({lines} lines)")