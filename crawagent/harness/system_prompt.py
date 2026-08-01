"""系统提示词组装模块

模块化系统提示词组装，支持动态注入站点特征、任务备注、用户追加指令。

参考 pi Agent Harness 的 modular prompt assembly 和 Deepsec 的 assemblePrompt：
- core: 核心身份 + 工具描述
- site_feature: 站点特征（动态注入）
- task_notes: 任务备注（动态注入）
- user_append: 用户追加指令
"""

from __future__ import annotations

from typing import Dict, List, Optional

from crawagent.harness.types import CrawlToolDef


# ==================== 核心系统提示词 ====================

CORE_SYSTEM_PROMPT = """你是 CrawAgent，一个专业的 AI 爬虫助手。你可以帮助用户：
1. 爬取和提取互联网内容
2. 监控网站变化
3. 检查网站安全漏洞
4. 整理数据到指定文件路径

你有以下工具可以使用：
{tool_descriptions}

工作原则：
- 优先使用低成本策略（HTTP 优先于浏览器）
- 遇到反爬时自动升级策略
- 提取的数据要干净无噪声
- 保存文件时使用用户指定的路径
- 遇到错误时分析原因并决定是否重试

输出格式：
- 工具调用使用标准 JSON 格式
- 总结时使用清晰的结构化 Markdown
"""

SITE_FEATURE_TEMPLATE = """
当前站点特征：
- URL: {url}
- 页面类型: {page_type}
- 数据源: {data_source}
- 反爬迹象: {anti_bot_signs}
- 分页方式: {pagination_type}
"""

TASK_NOTES_TEMPLATE = """
任务备注：
{task_notes}
"""

USER_APPEND_TEMPLATE = """
用户追加指令：
{user_append}
"""


# ==================== 提示词组装器 ====================

class SystemPromptAssembler:
    """模块化系统提示词组装
    
    参考 pi 的 modular prompt assembly + Deepsec 的 assemblePrompt：
    - core: 核心身份 + 工具描述
    - site_feature: 站点特征（动态注入）
    - task_notes: 任务备注（动态注入）
    - user_append: 用户追加指令
    """
    
    def __init__(self):
        self._core: str = CORE_SYSTEM_PROMPT
        self._site_feature: str = ""
        self._task_notes: str = ""
        self._user_append: str = ""
    
    def set_core(self, prompt: str) -> "SystemPromptAssembler":
        self._core = prompt
        return self
    
    def set_site_feature(
        self,
        url: str = "",
        page_type: str = "",
        data_source: str = "",
        anti_bot_signs: List[str] = None,
        pagination_type: str = "",
    ) -> "SystemPromptAssembler":
        self._site_feature = SITE_FEATURE_TEMPLATE.format(
            url=url,
            page_type=page_type,
            data_source=data_source,
            anti_bot_signs=", ".join(anti_bot_signs or []),
            pagination_type=pagination_type,
        )
        return self
    
    def set_task_notes(self, notes: str) -> "SystemPromptAssembler":
        self._task_notes = TASK_NOTES_TEMPLATE.format(task_notes=notes)
        return self
    
    def set_user_append(self, instructions: str) -> "SystemPromptAssembler":
        self._user_append = USER_APPEND_TEMPLATE.format(user_append=instructions)
        return self
    
    def assemble(self, tools: List[CrawlToolDef] = None) -> str:
        """组装最终系统提示词
        
        顺序：core + site_feature + task_notes + user_append
        """
        parts = []
        
        # Core（含工具描述）
        tool_descriptions = ""
        if tools:
            tool_descriptions = "\n".join(
                f"- {t.name}: {t.description}" for t in tools
            )
        parts.append(self._core.format(tool_descriptions=tool_descriptions))
        
        # Site feature
        if self._site_feature:
            parts.append(self._site_feature)
        
        # Task notes
        if self._task_notes:
            parts.append(self._task_notes)
        
        # User append
        if self._user_append:
            parts.append(self._user_append)
        
        return "\n".join(parts)
    
    def reset(self) -> "SystemPromptAssembler":
        """重置为默认状态（保留 core）"""
        self._site_feature = ""
        self._task_notes = ""
        self._user_append = ""
        return self
