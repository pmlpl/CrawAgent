"""一次性 — 检测潜在循环 import 对"""
import pathlib, re

root = pathlib.Path("crawagent")
imports = {}
for p in root.rglob("*.py"):
    if "__pycache__" in str(p):
        continue
    text = p.read_text(encoding="utf-8")
    rel = str(p.relative_to("crawagent")).replace("\\", "/").rstrip(".py").replace("/", ".")
    targets = set()
    for line in text.splitlines():
        m = re.match(r"\s*(?:from\s+(\S+)\s+import|import\s+(\S+))", line)
        if not m:
            continue
        target = m.group(1) or m.group(2)
        if target.startswith("crawagent."):
            parts = target.split(".")
            mod = parts[1] + (("." + parts[2]) if len(parts) > 2 else "")
            targets.add(mod)
    imports[rel] = targets

cycles = set()
for src, targets in imports.items():
    for t in targets:
        if t in imports and src in imports[t]:
            pair = tuple(sorted([src, t]))
            cycles.add(pair)

print("Potential circular import pairs:")
for c in sorted(cycles):
    print(" ", c[0], " <-> ", c[1])