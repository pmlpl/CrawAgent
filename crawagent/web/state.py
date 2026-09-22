"""WebUI 共享状态：Agent/checkpointer 单例与会话运行时数据。

server.py 与各 router 都需要访问这些全局单例，独立成模块以避免循环导入。
"""
from __future__ import annotations

import asyncio
import json
import os
import sqlite3
from collections import OrderedDict
from datetime import datetime
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.sqlite import SqliteSaver  # 保留：sqlite 后端类型兼容

from crawagent.config.settings import get_settings
from crawagent.graph.agent import get_agent as _build_agent, warm_cache as _warm_cache
from crawagent.observability.metrics import SessionMetrics
from crawagent.storage.checkpointer import build_checkpointer
from crawagent.storage.meta_store import get_meta_conn, reset_meta_conn

# 推送给前端的工具结果预览长度（_run_turn 与会话历史接口共用）
TOOL_RESULT_PREVIEW = 600


class _LRUDict(OrderedDict):
    """带容量上限的 LRU 字典：淘汰最久未访问的条目，防止长驻服务内存只增不减。

    pop / clear 等其余 API 均为 OrderedDict 原生，调用点无需修改。
    """

    def __init__(self, maxsize: int, name: str):
        super().__init__()
        self._maxsize = maxsize
        self._name = name

    def __setitem__(self, k, v) -> None:
        if k in self:
            self.move_to_end(k)
        super().__setitem__(k, v)
        while len(self) > self._maxsize:
            key, _ = self.popitem(last=False)
            print(f"[LRU] {self._name} evicted {key!r}")

    def __getitem__(self, k):
        v = super().__getitem__(k)
        self.move_to_end(k)
        return v

    def get(self, k, d=None):
        """dict.get 的 LRU 版本：读到值时把 key 移到队尾（最近使用）。

        Args:
            k: 键。
            d: 缺省值（键不存在时返回）。

        Returns:
            键对应的值，或缺省值。
        """
        if k in self:
            self.move_to_end(k)
            return self[k]
        return d

    def setdefault(self, k, d=None):
        """dict.setdefault 的 LRU 版本：键不存在时设置并移到队尾。

        Args:
            k: 键。
            d: 缺省值（键不存在时设置）。

        Returns:
            现有值或新设置的缺省值。
        """
        if k not in self:
            self[k] = d
        else:
            self.move_to_end(k)
        return self[k]


# ---- 全局单例：Agent 按模型 ID 缓存（同一模型跨会话复用），checkpointer 只建一次 ----
# 上限从 settings 读（.env 可覆盖）；_active_turns 不做 LRU——存的是运行中任务，淘汰会炸
_settings = get_settings()
_agents: dict[str, Any] = _LRUDict(_settings.max_cached_agents, "agents")
_checkpointer: BaseCheckpointSaver | None = None
_metrics: dict[str, SessionMetrics] = _LRUDict(_settings.max_tracked_sessions, "metrics")
_session_locks: dict[str, asyncio.Lock] = _LRUDict(_settings.max_tracked_sessions, "session_locks")
_active_turns: dict[str, dict[str, Any]] = {}  # session_id → {queue, loop, future, events, ...}


def get_checkpointer() -> BaseCheckpointSaver:
    """惰性创建 checkpointer（只连数据库，不依赖 API Key）。

    与 get_agent 分离：会话列表/历史这类"纯数据库读"操作，
    Agent 构建失败（如缺 API Key）时也必须能工作。

    后端由 settings.checkpoint_backend 决定（sqlite 默认 / redis 分布式），
    实际初始化逻辑在 storage.build_checkpointer() 中。
    session_titles/session_errors 表已迁至 storage.meta_store（独立 meta.db），
    与 checkpointer 解耦。
    """
    global _checkpointer
    if _checkpointer is None:
        _checkpointer = build_checkpointer(get_settings())
    return _checkpointer


def save_session_error(session_id: str, message: str) -> None:
    """记录会话最后一次轮次失败原因（新错误覆盖旧错误）。失败只打日志不阻断。"""
    try:
        conn = get_meta_conn()
        conn.execute(
            "INSERT INTO session_errors (thread_id, message, ts) VALUES (?, ?, ?) "
            "ON CONFLICT(thread_id) DO UPDATE SET message = excluded.message, ts = excluded.ts",
            (session_id, message[:500], datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        )
        conn.commit()
    except Exception as e:
        print(f"[ERROR_STORE] save_session_error({session_id}) 失败（忽略）: {e}")


def clear_session_error(session_id: str) -> None:
    """清除会话的失败记录（下一轮成功后调用：问题已解决，红条不再恢复）。"""
    try:
        conn = get_meta_conn()
        conn.execute("DELETE FROM session_errors WHERE thread_id = ?", (session_id,))
        conn.commit()
    except Exception as e:
        print(f"[ERROR_STORE] clear_session_error({session_id}) 失败（忽略）: {e}")


def get_session_error(session_id: str) -> dict[str, str] | None:
    """读取会话最后一次失败原因；无记录返回 None。"""
    try:
        row = get_meta_conn().execute(
            "SELECT message, ts FROM session_errors WHERE thread_id = ?", (session_id,)
        ).fetchone()
    except Exception:
        return None
    if not row:
        return None
    return {"message": row[0], "ts": row[1]}


def get_agent(model: str | None = None):
    """惰性创建 Agent（首次对话时才初始化，避免无 API Key 时服务起不来）。

    按模型 ID 分别缓存：对话页切换模型时用对应模型的 Key/URL 重建，
    同一模型的 Agent 跨会话复用。
    """
    key = model or "__default__"
    if key not in _agents:
        _agents[key] = _build_agent(checkpointer=get_checkpointer(), model=model)
    return _agents[key]


def reset_agent_cache() -> None:
    """失效全部 Agent + checkpointer 单例，下次访问时按新配置重建（设置保存后调用）"""
    global _checkpointer
    _agents.clear()
    _checkpointer = None
    # meta.db 连接也要重置：checkpoint_backend 切换时避免旧连接残留
    reset_meta_conn()
    # MCP 工具缓存也要失效：token/开关可能变了
    from crawagent.graph.skills import reset_mcp_cache
    reset_mcp_cache()


def warm_cache(model: str | None = None) -> None:
    """透传缓存预热（server.py 的 import 中转）。失败静默，不阻断对话轮次。"""
    try:
        _warm_cache(model)
    except Exception as e:
        print(f"[WARMUP] cache warmup failed (ignored): {e}")


def session_lock(session_id: str) -> asyncio.Lock:
    """每个会话一把锁：同一会话的轮次创建/重连订阅互斥"""
    if session_id not in _session_locks:
        _session_locks[session_id] = asyncio.Lock()
    return _session_locks[session_id]


# ---- 会话级 metrics 持久化（server 重启后恢复状态栏） ----

_metrics_dir = _settings.sessions_db_path.parent / "metrics"


def save_metrics(session_id: str, metrics: SessionMetrics) -> None:
    """把会话 metrics 落盘（每轮结束后调），server 重启后可恢复。

    每个会话一个 JSON 文件，原子写（tmp + replace）。写入失败只打日志不阻断。
    """
    try:
        _metrics_dir.mkdir(parents=True, exist_ok=True)
        path = _metrics_dir / f"{session_id}.json"
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(metrics.to_dict(), ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)  # 原子重命名（同文件系统）
    except Exception as e:
        print(f"[METRICS] save_metrics({session_id}) 失败（忽略）: {e}")


def load_persisted_metrics(session_id: str) -> SessionMetrics | None:
    """从磁盘加载会话 metrics（内存 LRU 缺失时兜底）。"""
    path = _metrics_dir / f"{session_id}.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return SessionMetrics.from_dict(data)
    except Exception as e:
        print(f"[METRICS] load_persisted_metrics({session_id}) 失败（忽略）: {e}")
        return None


def get_metrics(session_id: str) -> SessionMetrics | None:
    """读会话 metrics：内存 LRU 优先，缺失则从磁盘恢复并填回内存。"""
    m = _metrics.get(session_id)
    if m is not None:
        return m
    m = load_persisted_metrics(session_id)
    if m is not None:
        _metrics[session_id] = m  # 填回内存，后续命中
    return m
