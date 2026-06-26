"""LangGraph 工作流 —— CrawAgent 的大脑

提供两种工作流:
1. CrawWorkflow: 旧版顺序执行工作流（关键词匹配）
2. AgentWorkflow: 新版真正的 Agent 工作流（LLM 决策 + Think-Act-Observe-Reflect 循环）
"""
from .workflow import CrawState, CrawWorkflow, CrawlIntent
from .agent_workflow import AgentWorkflow, AgentState

__all__ = [
    # 旧版工作流（向后兼容）
    "CrawState", "CrawWorkflow", "CrawlIntent",
    # 新版 Agent 工作流
    "AgentWorkflow", "AgentState",
]
