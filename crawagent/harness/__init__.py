"""CrawlHarness - pi Agent Harness 的 Python 实现

核心组件：
- CrawlHarness: 编排层（session + hooks + lanes）
- CrawlLoop: LLM 调用循环（driverLoop）
- CrawlSession: MySQL 持久化会话
- CrawlHooks: 8 种 hook 事件拦截
- CrawlToolDef: 爬虫工具定义
- CrawlEnv: 执行环境抽象
- Compaction: 上下文压缩
- SystemPromptAssembler: 提示词组装
"""

from crawagent.harness.types import (
    Phase,
    HookEvent,
    CrawlMessage,
    TurnSnapshot,
    RunResult,
    OperationType,
    OperationRecord,
    LaneInfo,
    ToolExecMode,
    CrawlToolDef,
    TokenUsage,
)
from crawagent.harness.session import CrawlSession, SessionManager
from crawagent.harness.hooks import CrawlHooks, AntiBotHookHandler, create_antibot_hooks
from crawagent.harness.loop import CrawlLoop
from crawagent.harness.harness import CrawlHarness
from crawagent.harness.tools import ToolRegistry, create_default_tools
from crawagent.harness.env import CrawlEnv
from crawagent.harness.compaction import Compaction
from crawagent.harness.system_prompt import SystemPromptAssembler

__all__ = [
    # Types
    "Phase",
    "HookEvent",
    "CrawlMessage",
    "TurnSnapshot",
    "RunResult",
    "OperationType",
    "OperationRecord",
    "LaneInfo",
    "ToolExecMode",
    "CrawlToolDef",
    "TokenUsage",
    # Core
    "CrawlHarness",
    "CrawlLoop",
    "CrawlSession",
    "SessionManager",
    "CrawlHooks",
    "AntiBotHookHandler",
    "create_antibot_hooks",
    "CrawlEnv",
    "Compaction",
    "SystemPromptAssembler",
    # Tools
    "ToolRegistry",
    "create_default_tools",
]
