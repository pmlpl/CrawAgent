"""Skill 加载器 — 从 Markdown 文件动态加载 Skill 定义

Skill 文件格式（Markdown）：
```markdown
# Skill: 优酷视频批量抓取

name: youku_batch_scrape
description: 适合优酷专辑页，批量提取剧集并下载
trigger_keywords: [优酷, youku, 批量下载视频, 剧集]
examples:
  - "帮我下载优酷的这个视频"
  - "爬取优酷这个专辑的所有剧集"

prompt: |
  当用户请求优酷相关任务时：
  1. 首先判断是单视频还是专辑页
  2. 单视频 → 使用 packet_crawler 抓取流地址
  3. 专辑页 → 使用 crawl_show_preview 提取所有剧集
  4. 每个视频调用 ffmpeg 转换格式
  5. 输出格式：JSON 数组含标题/URL/下载状态

tags: [视频, 优酷, 批量, 抓包]
version: 1.0
```
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..config.settings import get_logger
logger = get_logger(__name__)


@dataclass
class Skill:
    """Skill 定义"""
    name: str
    description: str
    trigger_keywords: list[str] = field(default_factory=list)
    examples: list[str] = field(default_factory=list)
    prompt: str = ""
    tags: list[str] = field(default_factory=list)
    version: str = "1.0"
    file_path: str = ""

    def matches(self, user_input: str) -> bool:
        """检查用户输入是否匹配本 Skill 的触发关键词"""
        text_lower = user_input.lower()
        return any(kw.lower() in text_lower for kw in self.trigger_keywords)

    def to_system_prompt(self) -> str:
        """将 Skill 的 prompt 转换为可注入 LLM System Prompt 的片段"""
        return f"\n\n## Skill: {self.name}\n{self.prompt}"


class SkillLoader:
    """Skill 加载器 — 扫描目录、解析 Markdown、缓存 Skill"""

    def __init__(self, builtin_dir: str | Path, user_dir: str | Path | None = None):
        self.builtin_dir = Path(builtin_dir)
        self.user_dir = Path(user_dir) if user_dir else None
        self._skills: list[Skill] = []
        self._loaded = False

    def load(self) -> list[Skill]:
        """加载所有 Skill（builtin + user）"""
        if self._loaded:
            return self._skills

        # 1. 加载 builtin
        if self.builtin_dir.exists():
            for f in self.builtin_dir.glob("*.md"):
                skill = self._parse_markdown(f)
                if skill:
                    self._skills.append(skill)

        # 2. 加载 user（可选）
        if self.user_dir and self.user_dir.exists():
            for f in self.user_dir.glob("*.md"):
                skill = self._parse_markdown(f)
                if skill:
                    self._skills.append(skill)

        self._loaded = True
        return self._skills

    def _parse_markdown(self, file_path: Path) -> Skill | None:
        """解析 Markdown Skill 文件"""
        try:
            content = file_path.read_text(encoding="utf-8")
        except Exception as e:
            logger.error(f"[SkillLoader] 无法读取 {file_path}: {e}")
            return None

        # 提取字段
        name = self._extract_field(content, "name")
        description = self._extract_field(content, "description")
        trigger_keywords = self._extract_list_field(content, "trigger_keywords")
        examples = self._extract_list_field(content, "examples")
        prompt = self._extract_prompt(content)
        tags = self._extract_list_field(content, "tags")
        version = self._extract_field(content, "version") or "1.0"

        if not name:
            logger.warning(f"[SkillLoader] {file_path} 缺少 name 字段，跳过")
            return None

        return Skill(
            name=name,
            description=description or "",
            trigger_keywords=trigger_keywords,
            examples=examples,
            prompt=prompt,
            tags=tags,
            version=version,
            file_path=str(file_path),
        )

    def _extract_field(self, content: str, field_name: str) -> str | None:
        """提取单行字段（如 name: xxx）"""
        pattern = rf"^{field_name}:\s*(.+)$"
        match = re.search(pattern, content, re.MULTILINE)
        return match.group(1).strip() if match else None

    def _extract_list_field(self, content: str, field_name: str) -> list[str]:
        """提取列表字段（如 trigger_keywords: [a, b, c]）"""
        pattern = rf"^{field_name}:\s*\[([^\]]+)\]"
        match = re.search(pattern, content, re.MULTILINE)
        if not match:
            return []
        items = match.group(1).split(",")
        return [item.strip().strip("'\"") for item in items if item.strip()]

    def _extract_prompt(self, content: str) -> str:
        """提取 prompt 块（prompt: | 后的多行文本）"""
        pattern = r"^prompt:\s*\|?\s*\n((?:.+\n?)+)"
        match = re.search(pattern, content, re.MULTILINE)
        if not match:
            return ""
        return match.group(1).strip()

    def match(self, user_input: str) -> Skill | None:
        """匹配用户输入，返回第一个匹配的 Skill"""
        for skill in self._skills:
            if skill.matches(user_input):
                return skill
        return None

    def all_names(self) -> list[str]:
        """返回所有 Skill 名称"""
        return [s.name for s in self._skills]

    def get_by_name(self, name: str) -> Skill | None:
        """按名称获取 Skill"""
        for s in self._skills:
            if s.name == name:
                return s
        return None


# ============================================================
# 全局单例（方便其他模块直接调用）
# ============================================================

_BUILTIN_DIR = Path(__file__).parent / "builtins"
_USER_DIR = Path.home() / ".crawagent" / "skills"

_loader: SkillLoader | None = None


def get_loader() -> SkillLoader:
    """获取全局 SkillLoader 单例"""
    global _loader
    if _loader is None:
        _loader = SkillLoader(_BUILTIN_DIR, _USER_DIR)
        _loader.load()
    return _loader


def load_all_skills() -> list[Skill]:
    """加载所有 Skill"""
    return get_loader().load()


def match_skill(user_input: str) -> Skill | None:
    """匹配用户输入，返回对应的 Skill"""
    return get_loader().match(user_input)