"""产物磁盘清理：按保留天数 + 容量上限清理 logs/ output/ downloads/，默认 dry run。

用法：
    python scripts/prune_outputs.py                 # 只扫描打印候选（dry run）
    python scripts/prune_outputs.py --apply         # 真删
    python scripts/prune_outputs.py --logs-only     # 只处理 logs/
    python scripts/prune_outputs.py --outputs-only / --downloads-only

规则：每个目录先按保留天数（mtime）砍 → 再算剩余总容量，超过 max_size_gb
就从最老文件继续砍。天数/上限在 settings.py（或 .env）配置，None = 该维度不清理。
"""
import sys, io, argparse
from datetime import datetime, timezone
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from crawagent.config.settings import get_settings

GB = 1024 ** 3


def _iter_files(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return [p for p in root.rglob("*") if p.is_file()]


def _prune_dir(root: Path, days: int | None, max_gb: float | None, label: str, apply: bool) -> None:
    files = _iter_files(root)
    if not files:
        print(f"[{label}] {root} 不存在或为空，跳过")
        return
    now = datetime.now(timezone.utc).timestamp()
    victims: set[Path] = set()

    # 1) 按保留天数砍
    if days is not None:
        cutoff = now - days * 86400
        for p in files:
            if p.stat().st_mtime < cutoff:
                victims.add(p)

    # 2) 按容量上限砍（从最老文件继续）
    if max_gb is not None:
        survivors = sorted((p for p in files if p not in victims), key=lambda p: p.stat().st_mtime)
        total = sum(p.stat().st_size for p in survivors)
        limit = max_gb * GB
        for p in survivors:
            if total <= limit:
                break
            victims.add(p)
            total -= p.stat().st_size

    if not victims:
        size = sum(p.stat().st_size for p in files) / GB
        print(f"[{label}] {len(files)} 个文件共 {size:.2f}GB，无清理候选")
        return

    freed = sum(p.stat().st_size for p in victims)
    print(f"[{label}] 候选 {len(victims)} 个文件，释放 {freed / GB:.2f}GB：")
    for p in sorted(victims, key=lambda p: p.stat().st_mtime)[:20]:
        print(f"    {datetime.fromtimestamp(p.stat().st_mtime):%Y-%m-%d}  {p.relative_to(root)}")
    if len(victims) > 20:
        print(f"    ... 及另外 {len(victims) - 20} 个")

    if not apply:
        return
    for p in victims:
        try:
            p.unlink()
        except OSError as e:
            print(f"    删除失败 {p}: {e}")
    # 顺带清掉砍空了的子目录（不含根目录本身）
    for d in sorted((q for q in root.rglob("*") if q.is_dir()), reverse=True):
        try:
            d.rmdir()
        except OSError:
            pass
    print(f"[{label}] 已删除 {len(victims)} 个文件")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="实际执行（默认 dry run）")
    ap.add_argument("--logs-only", action="store_true")
    ap.add_argument("--outputs-only", action="store_true")
    ap.add_argument("--downloads-only", action="store_true")
    args = ap.parse_args()

    s = get_settings()
    targets = [
        ("logs", s.log_dir, s.logs_retention_days, None),
        ("output", s.output_dir, s.output_retention_days, s.output_max_size_gb),
        ("downloads", s.downloads_dir, s.downloads_retention_days, s.downloads_max_size_gb),
    ]
    if args.logs_only:
        targets = [t for t in targets if t[0] == "logs"]
    if args.outputs_only:
        targets = [t for t in targets if t[0] == "output"]
    if args.downloads_only:
        targets = [t for t in targets if t[0] == "downloads"]

    for label, root, days, max_gb in targets:
        _prune_dir(root, days, max_gb, label, args.apply)

    if not args.apply:
        print("\n(dry run，加 --apply 执行)")


if __name__ == "__main__":
    main()
