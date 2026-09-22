"""一次性审计 — shell/subprocess 风险点"""
import pathlib

root = pathlib.Path("crawagent")
hits = []
for p in root.rglob("*.py"):
    if "__pycache__" in str(p):
        continue
    text = p.read_text(encoding="utf-8")
    for i, line in enumerate(text.splitlines(), 1):
        if "shell=True" in line:
            hits.append((str(p.relative_to(".")), i, "RISK shell=True", line.strip()))
        elif "subprocess.Popen" in line and "shell=" not in line:
            hits.append((str(p.relative_to(".")), i, "Popen no shell=", line.strip()))
print("subprocess/shell risky uses:")
for h in hits:
    print(" ", h)