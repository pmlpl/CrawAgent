"""pi Agent Harness - Python 类型映射

本模块定义了 Agent Harness 的核心类型系统，参考 pi Agent Harness 的 TypeScript 类型设计，
用 Python (dataclass / enum / Pydantic) 实现。

主要类型：
- Phase: Agent 运行阶段枚举
- HookEvent: Hook 事件枚举
- CrawlMessage: 对话树中的消息条目
- TurnSnapshot: 每轮不可变快照
- RunResult: 操作方法返回结果（判别联合）
- OperationType / OperationRecord: 操作日志
- LaneInfo: Lane 信息
- ToolExecMode / CrawlToolDef: 爬虫工具定义
- TokenUsage: Token 使用统计
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional

from pydantic import BaseModel


# ---------------------------------------------------------------------------
# Phase - Agent 运行阶段
# ---------------------------------------------------------------------------

class Phase(str, Enum):
    """Agent 运行阶段枚举。"""
    IDLE = "idle"
    TURN = "turn"
    COMPACTION = "compaction"
    BRANCH_SUMMARY = "branch_summary"
    RETRY = "retry"


# ---------------------------------------------------------------------------
# HookEvent - Hook 事件
# ---------------------------------------------------------------------------

class HookEvent(str, Enum):
    """8 种 Hook 事件枚举。"""
    BEFORE_RUN = "before_run"
    BEFORE_TOOL = "before_tool"
    AFTER_TOOL = "after_tool"
    TRANSFORM_CONTEXT = "transform_context"
    BEFORE_REQUEST = "before_request"
    AFTER_RESPONSE = "after_response"
    BEFORE_COMPACTION = "before_compaction"
    AFTER_COMPACTION = "after_compaction"
    BEFORE_CRASH_RECOVERY = "before_crash_recovery"
    AFTER_CRASH_RECOVERY = "after_crash_recovery"
    BEFORE_RUN_END = "before_run_end"


# ---------------------------------------------------------------------------
# CrawlMessage - 对话树中的消息条目
# ---------------------------------------------------------------------------

@dataclass
class CrawlMessage:
    """对话树中的消息条目。"""
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    parent_id: Optional[str] = None
    role: str = "user"
    content: str = ""
    tool_calls: List[Dict] = field(default_factory=list)
    tool_call_id: Optional[str] = None
    metadata: Dict = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)


# ---------------------------------------------------------------------------
# TurnSnapshot - 每轮不可变快照
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TurnSnapshot:
    """每轮不可变快照，冻结后不可修改。"""
    turn_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    phase: Phase = Phase.IDLE
    messages: List[CrawlMessage] = field(default_factory=list)
    tools: List[str] = field(default_factory=list)
    system_prompt: str = ""
    lane_name: str = ""
    created_at: float = field(default_factory=time.time)


# ---------------------------------------------------------------------------
# RunResult - 操作方法返回结果（判别联合）
# ---------------------------------------------------------------------------

class RunResult(BaseModel):
    """操作方法返回结果，通过 kind 字段判别类型。

    kind 取值：
    - "completed": 正常完成
    - "needs_input": 需要用户输入
    - "error": 发生错误
    - "cancelled": 已取消
    """
    kind: str
    data: Optional[Dict] = None
    error: Optional[str] = None
    follow_up: Optional[str] = None

    @classmethod
    def completed(cls, data: Optional[Dict] = None, follow_up: Optional[str] = None) -> RunResult:
        return cls(kind="completed", data=data, follow_up=follow_up)

    @classmethod
    def needs_input(cls, follow_up: Optional[str] = None) -> RunResult:
        return cls(kind="needs_input", follow_up=follow_up)

    @classmethod
    def from_error(cls, error: str) -> RunResult:
        """工厂方法：创建错误结果（避免与 error 字段同名冲突）"""
        return cls(kind="error", error=error)

    @classmethod
    def cancelled(cls) -> RunResult:
        return cls(kind="cancelled")


# ---------------------------------------------------------------------------
# OperationType - 操作日志类型
# ---------------------------------------------------------------------------

class OperationType(str, Enum):
    """操作日志类型枚举。"""
    RUN = "run"
    COMPACTION = "compaction"
    NAVIGATION = "navigation"


# ---------------------------------------------------------------------------
# OperationRecord - 操作日志记录
# ---------------------------------------------------------------------------

@dataclass
class OperationRecord:
    """操作日志记录。"""
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    lane_name: str = ""
    op_type: OperationType = OperationType.RUN
    started_at: float = field(default_factory=time.time)
    finished_at: Optional[float] = None
    entries: List[str] = field(default_factory=list)
    error: Optional[str] = None

    @property
    def is_open(self) -> bool:
        return self.finished_at is None

    @property
    def duration_seconds(self) -> Optional[float]:
        if self.finished_at is None:
            return None
        return max(0.0, self.finished_at - self.started_at)


# ---------------------------------------------------------------------------
# RecoveredOperation / RecoveryPlan - 崩溃恢复
# ---------------------------------------------------------------------------

class RecoveryAction(str, Enum):
    """对未完成操作的恢复策略。"""
    REPLAY_SAFE = "replay_safe"     # 幂等操作 → 直接重放
    MARK_FAILED = "mark_failed"     # 不重放 → 标记 failed 给上层决定
    RETRY = "retry"                 # 上层决定重试（由 CrawlLoop 负责）


@dataclass
class RecoveredOperation:
    """崩溃恢复中单个未完成操作的恢复建议。"""
    record: OperationRecord
    action: RecoveryAction = RecoveryAction.MARK_FAILED
    reason: str = ""
    # 重放时用到的上下文锚点：该 operation 开始前的 leaf_id
    # 由 add_operation_entry 时 lane leaf 在"第一个 entry 之前"的 leaf_id
    anchor_leaf_id: Optional[str] = None
    lane_name: str = ""


@dataclass
class RecoveryPlan:
    """崩溃恢复的总体恢复计划。"""
    session_id: str = ""
    recovered_at: float = field(default_factory=time.time)
    operations: List[RecoveredOperation] = field(default_factory=list)
    open_lanes: List[str] = field(default_factory=list)
    # 需要在"重建会话上下文"阶段移除的孤儿 tool/tool_call 消息（未完成的 tool call）
    orphan_entry_ids: List[str] = field(default_factory=list)

    def to_summary(self) -> str:
        by_action: Dict[str, int] = {}
        for op in self.operations:
            by_action[op.action.value] = by_action.get(op.action.value, 0) + 1
        parts = [
            f"[RecoveryPlan] session={self.session_id}",
            f"  open_ops={len(self.operations)} actions={by_action}",
            f"  open_lanes={self.open_lanes}",
            f"  orphan_entries={len(self.orphan_entry_ids)}",
        ]
        return "\n".join(parts)


# ---------------------------------------------------------------------------
# LaneInfo - Lane 信息
# ---------------------------------------------------------------------------

@dataclass
class LaneInfo:
    """Lane 信息。"""
    name: str = ""
    leaf_id: Optional[str] = None
    has_open_operation: bool = False


# ---------------------------------------------------------------------------
# ToolExecMode - 工具执行模式
# ---------------------------------------------------------------------------

class ToolExecMode(str, Enum):
    """工具执行模式枚举。"""
    SEQUENTIAL = "sequential"
    PARALLEL = "parallel"


# ---------------------------------------------------------------------------
# CrawlToolDef - 爬虫工具定义
# ---------------------------------------------------------------------------

@dataclass
class CrawlToolDef:
    """爬虫工具定义。"""
    name: str = ""
    description: str = ""
    parameters: Dict = field(default_factory=dict)
    exec_mode: ToolExecMode = ToolExecMode.SEQUENTIAL
    replay_safe: bool = False


# ---------------------------------------------------------------------------
# TokenUsage - Token 使用统计
# ---------------------------------------------------------------------------

@dataclass
class TokenUsage:
    """Token 使用统计。"""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    model: str = ""
