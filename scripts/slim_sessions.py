"""会话瘦身工具：尝试会话直接删，正常会话瘦身（保留问答、丢弃工具全文输出），先归档再动手。

用法：
    python scripts/slim_sessions.py            # 只报告，不动数据（dry run）
    python scripts/slim_sessions.py --apply    # 执行归档 + 删除/瘦身 + VACUUM
"""
import sys, io, json, sqlite3, argparse
from datetime import datetime
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from crawagent.config.settings import get_settings
from langgraph.checkpoint.sqlite import SqliteSaver
from langchain_core.messages import HumanMessage, AIMessage, ToolMessage


def human_turns(msgs):
    return sum(1 for m in msgs if isinstance(m, HumanMessage))


def text_size(msgs):
    total = 0
    for m in msgs:
        c = m.content if isinstance(m.content, str) else str(m.content)
        total += len(c)
        if isinstance(m, AIMessage) and m.tool_calls:
            total += sum(len(str(tc.get("args", ""))) for tc in m.tool_calls)
    return total


def slim(msgs):
    """保留 Human + 无 tool_calls 的 AIMessage；丢弃 ToolMessage 与工具调用骨架。"""
    return [m for m in msgs if isinstance(m, (HumanMessage, AIMessage)) and not (isinstance(m, AIMessage) and m.tool_calls)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="实际执行（默认 dry run）")
    args = ap.parse_args()

    settings = get_settings()
    conn = sqlite3.connect(str(settings.sessions_db_path), check_same_thread=False)
    cp = SqliteSaver(conn)

    thread_ids = [r[0] for r in conn.execute(
        "SELECT DISTINCT thread_id FROM checkpoints ORDER BY thread_id")]
    print(f"共 {len(thread_ids)} 个会话\n")

    plans = []
    for tid in thread_ids:
        t = cp.get_tuple({"configurable": {"thread_id": tid}})
        msgs = t.checkpoint["channel_values"].get("messages", []) if t else []
        turns, size = human_turns(msgs), text_size(msgs)
        tool_blobs = sum(len(m.content) for m in msgs if isinstance(m, ToolMessage))
        if turns <= 1 and size < 200:
            action = "DELETE"
        else:
            action = "SLIM"
        plans.append((tid, turns, len(msgs), size, tool_blobs, action, msgs, t))

    del_count = sum(1 for p in plans if p[5] == "DELETE")
    slim_savings = sum(p[4] for p in plans if p[5] == "SLIM")
    for tid, turns, n, size, tool_blobs, action, *_ in plans:
        print(f"  {action:6s} turns={turns} msgs={n} text={size//1024}KB toolblob={tool_blobs//1024}KB  {tid}")
    print(f"\n计划: 删除 {del_count} 个尝试会话, 瘦身 {len(plans)-del_count} 个会话(释放约 {slim_savings//1024//1024}MB 工具输出)")

    if not args.apply:
        print("\n(dry run，加 --apply 执行)")
        return

    archive_dir = settings.project_root / "logs" / "session_archives"
    archive_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    for tid, turns, n, size, tool_blobs, action, msgs, t in plans:
        # 1) 全量归档（无论删还是瘦，都可找回）
        archive = {
            "session_id": tid, "archived_at": ts, "action": action,
            "messages": [{"type": type(m).__name__, "content": m.content if isinstance(m.content, str) else str(m.content)}
                          for m in msgs],
        }
        (archive_dir / f"{tid}_{ts}.json").write_text(
            json.dumps(archive, ensure_ascii=False, indent=1), encoding="utf-8")

        if action == "DELETE":
            conn.execute("DELETE FROM checkpoints WHERE thread_id=?", (tid,))
            conn.execute("DELETE FROM writes WHERE thread_id=?", (tid,))
            print(f"  DELETED {tid} (已归档)")
            continue

        # 2) 瘦身：替换最新 checkpoint 的 channel_values，写回一个新 checkpoint
        slimmed = slim(msgs)
        checkpoint = dict(t.checkpoint)
        checkpoint["id"] = __import__("uuid").uuid4().hex
        checkpoint["ts"] = datetime.now().isoformat()
        checkpoint["channel_values"] = dict(t.checkpoint["channel_values"])
        checkpoint["channel_values"]["messages"] = slimmed
        cfg = t.config
        cp.put(cfg, checkpoint, t.metadata, {})
        # 3) 清掉该线程所有旧 checkpoint 行（新写入的行除外——按 checkpoint_id 保留最新）
        latest_id = checkpoint["id"]
        conn.execute("DELETE FROM checkpoints WHERE thread_id=? AND checkpoint_id != ?", (tid, latest_id))
        conn.execute("DELETE FROM writes WHERE thread_id=?", (tid,))
        print(f"  SLIMMED {tid}: {len(msgs)} -> {len(slimmed)} msgs (归档+新checkpoint)")

    conn.commit()
    print("\n执行 VACUUM（回收磁盘空间，需要几秒到几分钟）...")
    conn.execute("VACUUM")
    conn.commit()
    after = settings.sessions_db_path.stat().st_size / 1024 / 1024
    print(f"完成。sessions.db 现在 {after:.1f} MB")


if __name__ == "__main__":
    main()
