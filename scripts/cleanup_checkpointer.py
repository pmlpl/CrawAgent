"""046 checkpointer 定期清理 CLI——留最近 N 轮 state + 兜底校验 + VACUUM。

用户显式运行即授权（沿用 ADR-0008「不自动清理」纪律；不走 ask_user/UI）。
流程：
  1. 打印影响范围（每会话总轮数 / 将清理数 / conversation.md 兜底状态）；
  2. stdin 输入 y 二次确认；
  3. 每会话留 rowid 最大的 N 条、删老 state（删前校验该会话
     conversation.md 存在——不存在则跳过该会话不动其 state，防丢内容）；
  4. VACUUM 收缩 sqlite 文件（DELETE 不自动收缩，不 VACUUM 体积不降）；
  5. 报告：删除行数 / 跳过会话 / DB 体积前后对比。

用法：
  uv run python scripts/cleanup_checkpointer.py            # 交互（默认留 10 轮）
  uv run python scripts/cleanup_checkpointer.py --keep 5  # 自定义保留数
  uv run python scripts/cleanup_checkpointer.py --yes      # 跳过确认（自动化）
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

from crawagent.tools.session_folder import conversation_md_path

KEEP_N_DEFAULT = 10
SERVICE_PORT = 8006


def _count_turns_in_md(path: Path) -> int:
    """数 conversation.md 里 '## 轮次' 标题数（兜底校验参考）。"""
    if not path.exists():
        return 0
    try:
        return path.read_text(encoding="utf-8", errors="replace").count("\n## 轮次 ")
    except OSError:
        return 0


def _service_running() -> bool:
    """粗判 8006 端口是否在监听（在跑则警告，但不阻断——清理删老 state 不影响当前轮）。"""
    import socket

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(0.3)
    try:
        sock.connect(("127.0.0.1", SERVICE_PORT))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def run_cleanup(
    db_path: Path,
    sessions_root: Path,
    keep: int,
    auto_yes: bool = False,
    confirm_fn=input,
    print_fn=print,
) -> dict:
    """清理核心逻辑（可测：依赖全以参数注入）。

    返回 {deleted, skipped_no_md, size_before, size_after, planned_delete, vacuum_ok}。
    """
    if not db_path.exists():
        print_fn(f"[cleanup] 数据库不存在: {db_path}")
        return {"deleted": 0, "skipped_no_md": [], "size_before": 0, "size_after": 0,
                "planned_delete": 0, "vacuum_ok": False, "error": "db_not_found"}

    # autocommit：VACUUM 不能在事务里跑，DELETE 也无需回滚（留 N 逻辑保底）
    conn = sqlite3.connect(str(db_path), isolation_level=None)
    rows = conn.execute(
        "SELECT thread_id, COUNT(*) FROM checkpoints GROUP BY thread_id"
    ).fetchall()
    plan: list[tuple[str, int, int, bool, int]] = []
    for tid, total in rows:
        md_path = sessions_root / tid / "conversation.md"
        # 路径清洗防御（tid 理论安全，但脚本不假设）
        if not md_path.is_relative_to(sessions_root):
            md_path = sessions_root / "unknown" / "conversation.md"
        has_md = md_path.exists()
        md_turns = _count_turns_in_md(md_path) if has_md else 0
        will_delete = max(0, total - keep)
        plan.append((tid, total, will_delete, has_md, md_turns))

    total_delete = sum(p[2] for p in plan)
    print_fn("=" * 64)
    print_fn("checkpointer 定期清理影响范围")
    print_fn("=" * 64)
    print_fn(f"数据库: {db_path}")
    print_fn(f"保留策略: 每会话留最近 {keep} 轮 state")
    print_fn(f"会话数: {len(plan)} | 总 checkpoint 行: {sum(p[1] for p in plan)} | 将清理: {total_delete}")
    print_fn("-" * 64)
    for tid, total, will_del, has_md, md_turns in plan:
        md_flag = f"✓ conversation.md={md_turns}轮" if has_md else "✗ 无 conversation.md（跳过清理）"
        print_fn(f"  {tid[:20]:20s}  total={total:4d}  清={will_del:4d}  {md_flag}")
    print_fn("-" * 64)

    if total_delete == 0:
        print_fn("[cleanup] 没有需要清理的老 state，退出。")
        conn.close()
        return {"deleted": 0, "skipped_no_md": [], "size_before": db_path.stat().st_size,
                "size_after": db_path.stat().st_size, "planned_delete": 0, "vacuum_ok": True}

    if not auto_yes:
        print_fn("将删除上述老 state（conversation.md 不存在的会话会被跳过，不动其 state）。")
        confirm = confirm_fn("确认执行？输入 y 继续，其他取消: ").strip().lower()
        if confirm != "y":
            print_fn("[cleanup] 已取消。")
            conn.close()
            return {"deleted": 0, "skipped_no_md": [], "size_before": db_path.stat().st_size,
                    "size_after": db_path.stat().st_size, "planned_delete": total_delete,
                    "vacuum_ok": False, "cancelled": True}

    db_size_before = db_path.stat().st_size
    deleted = 0
    skipped_no_md: list[str] = []
    for tid, total, will_del, has_md, md_turns in plan:
        if will_del == 0:
            continue
        if not has_md:
            skipped_no_md.append(tid)
            continue
        conn.execute(
            "DELETE FROM checkpoints WHERE thread_id = ? AND rowid NOT IN ("
            "SELECT rowid FROM checkpoints WHERE thread_id = ? ORDER BY rowid DESC LIMIT ?)",
            (tid, tid, keep),
        )
        conn.execute(
            "DELETE FROM writes WHERE thread_id = ? AND rowid NOT IN ("
            "SELECT rowid FROM writes WHERE thread_id = ? ORDER BY rowid DESC LIMIT ?)",
            (tid, tid, keep),
        )
        deleted += will_del
        print_fn(f"[cleanup] {tid[:20]}  清理 {will_del} 条老 state")

    print_fn("[cleanup] VACUUM 收缩数据库文件...")
    vacuum_ok = True
    try:
        conn.execute("VACUUM")
    except sqlite3.OperationalError as e:
        print_fn(f"[cleanup] VACUUM 失败（可能库被占用）: {e}")
        vacuum_ok = False
    db_size_after = db_path.stat().st_size
    conn.close()

    print_fn("=" * 64)
    print_fn("清理完成")
    print_fn(f"删除 checkpoint 行: {deleted}")
    if skipped_no_md:
        print_fn(f"跳过会话（无 conversation.md，未动其 state）: {len(skipped_no_md)} 个")
        for tid in skipped_no_md:
            print_fn(f"  - {tid[:20]}")
    before_kb = db_size_before / 1024
    after_kb = db_size_after / 1024
    print_fn(f"DB 体积: {before_kb:.1f} KB → {after_kb:.1f} KB（-{(before_kb - after_kb):.1f} KB）")
    print_fn("=" * 64)
    return {"deleted": deleted, "skipped_no_md": skipped_no_md, "size_before": db_size_before,
            "size_after": db_size_after, "planned_delete": total_delete, "vacuum_ok": vacuum_ok}


def main() -> int:
    ap = argparse.ArgumentParser(
        description="046 checkpointer 定期清理（留最近 N 轮 + 兜底校验 + VACUUM）"
    )
    ap.add_argument(
        "--keep", type=int, default=KEEP_N_DEFAULT,
        help=f"每会话保留最近 N 轮 state（默认 {KEEP_N_DEFAULT}）",
    )
    ap.add_argument("--yes", action="store_true", help="跳过 stdin 确认（自动化用）")
    args = ap.parse_args()

    # 延迟 import + 模块属性方式：让测试能 monkeypatch get_settings
    from crawagent.config import settings as _cfg

    settings = _cfg.get_settings()
    db_path = settings.sessions_db_path
    sessions_root = settings.project_root / "data" / "sessions"

    if _service_running():
        print(f"[cleanup] ⚠ 检测到 {SERVICE_PORT} 端口在监听——建议停止服务后再清理，")
        print(f"           否则 VACUUM 可能因库锁失败。继续执行中...")

    result = run_cleanup(db_path, sessions_root, args.keep, args.yes)
    if not result.get("vacuum_ok"):
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
