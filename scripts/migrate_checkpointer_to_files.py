"""046 一次性迁移 CLI——老 checkpointer state 导出 conversation.md + 清老 state + VACUUM。

每会话 per-session 原子：导出 → 校验 → 清，部分失败不清该会话（state 原样保留）。

流程（规格 §3.4）：
  1. 前置：服务必须停止（8006 在跑则拒绝——避免与进行中轮次 checkpointer 写竞态）；
  2. 备份 sessions.db → sessions.db.bak-<timestamp>（不可逆操作前留后路）；
  3. per-session 原子：遍历 checkpoints 按 sid 分组 →
     a. conversation.md 已存在则跳过该会话（不合并——防破坏已有内容）；
     b. 取最新 checkpoint tuple 的 messages（LangGraph 全史累积，最新即全）；
     c. 按 HumanMessage 拆轮次 → init_conversation_md 建头 + 逐轮 write_turn_snapshot 追加；
     d. 校验：conversation.md 的「## 轮次」标题数 == 拆出的轮次数；
     e. 校验通过才清该会话老 state（留最近 N 轮，与 cleanup 一致）；
     f. 校验失败 / 导出异常 → 该会话 state 原样保留，继续下一个；
  4. VACUUM 收缩 DB；
  5. 报告：迁移会话数 / 导出轮次数 / 跳过 / 失败 / DB 体积前后对比。

用法（必须用 uv run——脚本依赖 langgraph 反序列化）：
  uv run python scripts/migrate_checkpointer_to_files.py            # 默认留最近 10 轮
  uv run python scripts/migrate_checkpointer_to_files.py --keep 5
"""
from __future__ import annotations

import argparse
import datetime
import json
import shutil
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage, SystemMessage, ToolMessage

from crawagent.tools.session_folder import (
    conversation_md_path,
    init_conversation_md,
    write_turn_snapshot,
)

KEEP_N_DEFAULT = 10
SERVICE_PORT = 8006


def _service_running() -> bool:
    """8006 端口是否在监听。迁移必须服务停止（避免 checkpointer 写竞态）。"""
    import socket

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(0.5)
    try:
        sock.connect(("127.0.0.1", SERVICE_PORT))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def _count_turns_in_md(path: Path) -> int:
    """数 conversation.md 里 '## 轮次' 标题数。"""
    if not path.exists():
        return 0
    try:
        return path.read_text(encoding="utf-8", errors="replace").count("\n## 轮次 ")
    except OSError:
        return 0


def _messages_to_turns(messages: list) -> list[list]:
    """扁平 messages 拆成轮次（每个 HumanMessage 开始一轮）。

    SystemMessage / RemoveMessage / 轮前孤儿消息跳过（不进 conversation.md）。
    """
    turns: list[list] = []
    current: list = []
    for m in messages:
        if isinstance(m, (SystemMessage, RemoveMessage)):
            continue
        if isinstance(m, HumanMessage):
            if current:
                turns.append(current)
            current = [m]
        else:
            if current:
                current.append(m)
    if current:
        turns.append(current)
    return turns


def _turn_to_snapshot(turn_msgs: list, turn_no: int) -> dict:
    """一轮 messages 转 write_turn_snapshot 的 turn dict（tool_call_id 配对）。"""
    tc_name: dict[str, str] = {}
    tc_args: dict[str, str] = {}
    for m in turn_msgs:
        if isinstance(m, AIMessage):
            for tc in m.tool_calls or []:
                tc_name[tc["id"]] = tc["name"]
                tc_args[tc["id"]] = json.dumps(tc.get("args", {}), ensure_ascii=False)
    tool_results: dict[str, str] = {}
    for m in turn_msgs:
        if isinstance(m, ToolMessage):
            c = m.content if isinstance(m.content, str) else str(m.content)
            tool_results[m.tool_call_id] = c
    tool_calls = [
        {"name": tc_name.get(tid, "?"), "args": tc_args.get(tid, ""), "result": tool_results.get(tid, "")}
        for tid in tc_name
    ]
    ai_text = ""
    for m in reversed(turn_msgs):
        if isinstance(m, AIMessage) and not m.tool_calls:
            ai_text = m.content if isinstance(m.content, str) else str(m.content)
            break
    user_text = ""
    for m in turn_msgs:
        if isinstance(m, HumanMessage):
            user_text = m.content if isinstance(m.content, str) else str(m.content)
            break
    return {"turn_no": turn_no, "user_text": user_text, "tool_calls": tool_calls, "ai_text": ai_text}


def _export_session(saver, sid: str, settings, keep: int) -> dict:
    """导出单个会话 messages → conversation.md，校验（不清 state，由调用方清）。

    返回 {sid, status, turns, reason}。status:
      ok / skipped_empty / skipped_existing / failed
    saver: duck-typed，需有 .list(config, limit) 方法（langgraph SqliteSaver 或 mock）。
    """
    md_path = conversation_md_path(sid, settings)
    if md_path.exists():
        return {"sid": sid, "status": "skipped_existing", "turns": 0,
                "reason": "conversation.md 已存在，跳过（不合并）"}

    config = {"configurable": {"thread_id": sid}}
    try:
        tuples = list(saver.list(config, limit=1))
    except Exception as e:
        return {"sid": sid, "status": "failed", "turns": 0, "reason": f"list 失败: {e}"}
    if not tuples:
        return {"sid": sid, "status": "skipped_empty", "turns": 0, "reason": "checkpointer 无记录"}

    cv = tuples[0].checkpoint.get("channel_values", {})
    messages = cv.get("messages", [])
    turns = _messages_to_turns(messages)
    if not turns:
        return {"sid": sid, "status": "skipped_empty", "turns": 0, "reason": "messages 为空或无轮次"}

    try:
        init_conversation_md(sid, settings)
        for i, turn_msgs in enumerate(turns, 1):
            snap = _turn_to_snapshot(turn_msgs, i)
            write_turn_snapshot(sid, snap, settings)
    except Exception as e:
        return {"sid": sid, "status": "failed", "turns": len(turns),
                "reason": f"导出失败: {e}"}

    actual = _count_turns_in_md(md_path)
    if actual != len(turns):
        return {"sid": sid, "status": "failed", "turns": len(turns),
                "reason": f"校验失败: md={actual} 轮 vs 预期={len(turns)}"}
    return {"sid": sid, "status": "ok", "turns": len(turns), "reason": ""}


def _default_saver_factory(conn):
    """默认 saver 工厂：langgraph SqliteSaver（不调 setup——读已有数据不建表）。"""
    from langgraph.checkpoint.sqlite import SqliteSaver

    return SqliteSaver(conn)


def run_migrate(
    db_path: Path,
    settings,
    keep: int,
    auto_yes: bool = False,
    confirm_fn=input,
    print_fn=print,
    saver_factory=None,
) -> dict:
    """迁移核心逻辑（可测：依赖全以参数注入）。

    返回 {ok, skipped_existing, skipped_empty, failed, total_turns, size_before, size_after}。
    """
    if not db_path.exists():
        print_fn(f"[migrate] 数据库不存在: {db_path}")
        return {"ok": 0, "skipped_existing": 0, "skipped_empty": 0, "failed": 0,
                "total_turns": 0, "size_before": 0, "size_after": 0, "error": "db_not_found"}

    if saver_factory is None:
        saver_factory = _default_saver_factory

    # 备份
    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    bak_path = db_path.with_suffix(db_path.suffix + f".bak-{ts}")
    print_fn(f"[migrate] 备份 {db_path} → {bak_path}")
    shutil.copy2(db_path, bak_path)

    conn = sqlite3.connect(str(db_path), isolation_level=None)
    saver = saver_factory(conn)
    thread_rows = conn.execute(
        "SELECT thread_id, COUNT(*) FROM checkpoints GROUP BY thread_id ORDER BY MAX(rowid) DESC"
    ).fetchall()
    print_fn("=" * 64)
    print_fn(f"迁移影响范围: {len(thread_rows)} 个会话, 保留策略留最近 {keep} 轮")
    print_fn("=" * 64)

    if not auto_yes:
        confirm = confirm_fn("将导出老会话 messages 到 conversation.md 并清老 state。输入 y 继续: ").strip().lower()
        if confirm != "y":
            print_fn("[migrate] 已取消。")
            conn.close()
            return {"ok": 0, "skipped_existing": 0, "skipped_empty": 0, "failed": 0,
                    "total_turns": 0, "size_before": db_path.stat().st_size,
                    "size_after": db_path.stat().st_size, "cancelled": True, "backup": str(bak_path)}

    db_size_before = db_path.stat().st_size
    results = []
    cleaned = 0
    for tid, total in thread_rows:
        r = _export_session(saver, tid, settings, keep)
        results.append(r)
        status = r["status"]
        if status == "ok":
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
            cleaned += 1
            print_fn(f"[migrate] ✓ {tid[:20]}  导出 {r['turns']} 轮 → conversation.md，清老 state")
        elif status == "skipped_existing":
            print_fn(f"[migrate] → {tid[:20]}  conversation.md 已存在，跳过（不动 state）")
        elif status == "skipped_empty":
            print_fn(f"[migrate] · {tid[:20]}  {r['reason']}，不动 state")
        else:
            print_fn(f"[migrate] ✗ {tid[:20]}  {r['reason']}，state 原样保留")

    print_fn("[migrate] VACUUM 收缩数据库文件...")
    try:
        conn.execute("VACUUM")
    except sqlite3.OperationalError as e:
        print_fn(f"[migrate] VACUUM 失败: {e}")
    db_size_after = db_path.stat().st_size
    conn.close()

    ok = sum(1 for r in results if r["status"] == "ok")
    skipped_e = sum(1 for r in results if r["status"] == "skipped_existing")
    skipped_empty = sum(1 for r in results if r["status"] == "skipped_empty")
    failed = sum(1 for r in results if r["status"] == "failed")
    total_turns = sum(r["turns"] for r in results if r["status"] == "ok")
    print_fn("=" * 64)
    print_fn("迁移完成")
    print_fn(f"导出+清理: {ok} 会话（共 {total_turns} 轮写入 conversation.md）")
    print_fn(f"跳过(已存在 md): {skipped_e} | 跳过(空): {skipped_empty} | 失败: {failed}")
    print_fn(f"备份: {bak_path}")
    before_kb = db_size_before / 1024
    after_kb = db_size_after / 1024
    print_fn(f"DB 体积: {before_kb:.1f} KB → {after_kb:.1f} KB（-{(before_kb - after_kb):.1f} KB）")
    print_fn("=" * 64)
    return {"ok": ok, "skipped_existing": skipped_e, "skipped_empty": skipped_empty,
            "failed": failed, "total_turns": total_turns, "size_before": db_size_before,
            "size_after": db_size_after, "backup": str(bak_path)}


def main() -> int:
    ap = argparse.ArgumentParser(
        description="046 一次性迁移：checkpointer state → conversation.md + 清老 state + VACUUM"
    )
    ap.add_argument(
        "--keep", type=int, default=KEEP_N_DEFAULT,
        help=f"清理时每会话保留最近 N 轮 state（默认 {KEEP_N_DEFAULT}）",
    )
    ap.add_argument("--yes", action="store_true", help="跳过 stdin 确认")
    args = ap.parse_args()

    # 延迟 import + 模块属性方式：让测试能 monkeypatch get_settings
    from crawagent.config import settings as _cfg

    settings = _cfg.get_settings()
    db_path = settings.sessions_db_path

    if _service_running():
        print(f"[migrate] ✗ 检测到 {SERVICE_PORT} 端口在监听——迁移必须服务停止，")
        print(f"           避免 checkpointer 写竞态。请先停止 crawagent start，再跑迁移。")
        return 1

    result = run_migrate(db_path, settings, args.keep, args.yes)
    if result.get("failed"):
        print("[migrate] 有失败会话，state 已原样保留，可重跑（已成功的会话 conversation.md 已存在会跳过）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
