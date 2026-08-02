"""文件整理器（P3-1）：路径模板引擎 + 非法字符转义

支持路径模板占位符：
- {domain}    : URL 域名（如 www.ruanyifeng.com）
- {date}      : 当前日期 YYYY-MM-DD
- {year}/{month}/{day} : 日期分量
- {title}     : 文章标题（自动转义非法字符）
- {ext}       : 文件扩展名（如 md/json/csv）
- {slug}      : 标题转 slug（小写+连字符）

非法字符转义：Windows 文件名禁止 ``\\ / : * ? " < > |`` ，统一替换为 _
"""
from __future__ import annotations

import os
import re
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlparse

from loguru import logger


# Windows / Linux 文件名禁止字符
_ILLEGAL_CHARS = re.compile(r'[\\/:*?"<>|]')
# 多个下划线合并
_MULTI_UNDERSCORE = re.compile(r"_+")


def sanitize_filename(name: str, max_len: int = 120) -> str:
    """转义文件名中的非法字符。

    - \\ / : * ? " < > | → _
    - 去除首尾空格和点（Windows 不允许末尾点）
    - 合并多个下划线
    - 截断到 max_len 字符（避免路径过长）
    """
    if not name:
        return "untitled"
    # NFKC 规范化（全角→半角）
    name = unicodedata.normalize("NFKC", name)
    # 非法字符替换
    name = _ILLEGAL_CHARS.sub("_", name)
    # 控制字符删除
    name = re.sub(r"[\x00-\x1f]", "", name)
    # 合并下划线
    name = _MULTI_UNDERSCORE.sub("_", name)
    # 去除首尾空格、点、下划线
    name = name.strip(" ._\t\n\r")
    if not name:
        return "untitled"
    # 截断（保留扩展名）
    if len(name) > max_len:
        name = name[:max_len].rstrip(" ._\t\n\r")
    return name or "untitled"


def title_to_slug(title: str, max_len: int = 80) -> str:
    """标题转 slug：小写 + 非字母数字转连字符。

    中文标题保留原字符（不强制转拼音，避免引入 pypinyin 依赖）。
    """
    if not title:
        return "untitled"
    # NFKC 规范化
    s = unicodedata.normalize("NFKC", title).strip().lower()
    # 中文字符、字母、数字保留；其他转 -
    s = re.sub(r"[^\u4e00-\u9fa5a-z0-9]+", "-", s)
    # 合并多个连字符
    s = re.sub(r"-+", "-", s)
    s = s.strip("-")
    if not s:
        return "untitled"
    if len(s) > max_len:
        s = s[:max_len].rstrip("-")
    return s


def extract_domain(url: str) -> str:
    """从 URL 提取域名（去除 www. 前缀）。"""
    if not url:
        return "unknown"
    try:
        netloc = urlparse(url).netloc.lower()
        if netloc.startswith("www."):
            netloc = netloc[4:]
        # 保留端口号
        return netloc or "unknown"
    except Exception:
        return "unknown"


def unique_path(path: str) -> str:
    """同名文件去重：存在时追加 -2 / -3 后缀，避免覆盖。"""
    if not os.path.exists(path):
        return path
    base, ext = os.path.splitext(path)
    i = 2
    candidate = f"{base}-{i}{ext}"
    while os.path.exists(candidate):
        i += 1
        candidate = f"{base}-{i}{ext}"
    return candidate


class FileOrganizer:
    """路径模板引擎：把占位符模板渲染为实际文件路径。

    用法：
        org = FileOrganizer(base_dir="~/crawagent")
        path = org.render(
            template="articles/{domain}/{date}/{title}.{ext}",
            url="https://www.ruanyifeng.com/blog/2024/01/foo.html",
            title="Hello World: 测试",
            ext="md",
        )
        # → ~/crawagent/articles/www.ruanyifeng.com/2024-01-15/Hello_World_测试.md
    """

    def __init__(self, base_dir: str = "./output"):
        self.base_dir = os.path.expanduser(base_dir)

    def render(
        self,
        template: str,
        url: str = "",
        title: str = "",
        ext: str = "md",
        extra: Optional[Dict[str, str]] = None,
    ) -> str:
        """渲染路径模板。

        Args:
            template: 路径模板，如 "articles/{domain}/{date}/{title}.{ext}"
            url: 源 URL（用于提取 domain）
            title: 文章标题（自动转义）
            ext: 文件扩展名
            extra: 额外占位符（覆盖默认）

        Returns:
            绝对文件路径（已展开 ~ 和 base_dir）
        """
        now = datetime.now()
        domain = extract_domain(url)
        safe_title = sanitize_filename(title)
        slug = title_to_slug(title)

        variables: Dict[str, str] = {
            "domain": sanitize_filename(domain),
            "date": now.strftime("%Y-%m-%d"),
            "year": now.strftime("%Y"),
            "month": now.strftime("%m"),
            "day": now.strftime("%d"),
            "time": now.strftime("%H%M%S"),
            "title": safe_title,
            "slug": slug,
            "ext": ext.lstrip("."),
            "url": url,
        }
        if extra:
            for k, v in extra.items():
                variables[k] = sanitize_filename(str(v))

        # 渲染模板（使用 str.format_map，缺失 key 保留原样不报错）
        try:
            rendered = template.format_map(_SafeDict(variables))
        except Exception:
            rendered = template

        # 拼接 base_dir（若 template 是相对路径）
        if not os.path.isabs(rendered) and not rendered.startswith(("~", "$")):
            rendered = os.path.join(self.base_dir, rendered)

        # 展开 ~ 和环境变量
        rendered = os.path.expanduser(os.path.expandvars(rendered))

        # 规范化路径分隔符
        rendered = os.path.normpath(rendered)
        return rendered

    def ensure_dir(self, path: str) -> str:
        """确保路径所在目录存在。"""
        dir_part = os.path.dirname(path) or "."
        os.makedirs(dir_part, exist_ok=True)
        return path

    def resolve_path(
        self,
        content: str = "",
        path: str = "",
        template: str = "",
        url: str = "",
        title: str = "",
        ext: str = "md",
    ) -> str:
        """智能解析保存路径：优先 path，其次 template，最后默认。

        - 若提供 path（直接路径），展开 ~ 并返回
        - 若提供 template（模板），渲染为路径
        - 都没有则用 {base_dir}/{domain}/{date}/{title}.{ext}
        """
        if path:
            return os.path.expanduser(os.path.expandvars(path))
        if template:
            return self.render(template, url=url, title=title, ext=ext)
        # 默认模板
        return self.render("{domain}/{date}/{title}.{ext}", url=url, title=title, ext=ext)

    def write(self, path: str, content: str, mode: str = "w", encoding: str = "utf-8") -> int:
        """写入文件，自动创建目录。返回写入字节数。"""
        self.ensure_dir(path)
        with open(path, mode, encoding=encoding) as f:
            f.write(content)
        return len(content.encode(encoding))

    def list_files(
        self,
        subdir: str = "",
        pattern: str = "**/*",
        limit: int = 200,
    ) -> list:
        """列出 base_dir 下（或 subdir 下）的文件。"""
        abs_base = Path(self.base_dir).resolve()
        search_dir = abs_base / subdir if subdir else abs_base
        if not search_dir.is_dir():
            return []
        results = []
        for p in search_dir.glob(pattern):
            if p.is_file():
                stat = p.stat()
                results.append({
                    "path": str(p.resolve()),
                    "relative_path": str(p.relative_to(abs_base)),
                    "name": p.name,
                    "size": stat.st_size,
                    "modified": stat.st_mtime,
                })
            if len(results) >= limit:
                break
        results.sort(key=lambda x: x.get("modified", 0), reverse=True)
        return results


class _SafeDict(dict):
    """format_map 用的安全字典：缺失 key 返回 {key} 原样。"""

    def __missing__(self, key):
        return "{" + key + "}"


def organize_content(
    content: str,
    url: str,
    title: str,
    template: str = "{domain}/{date}/{title}.{ext}",
    base_dir: str = "./output",
    ext: str = "md",
) -> str:
    """便捷函数：渲染路径并写入文件，返回最终路径。"""
    org = FileOrganizer(base_dir=base_dir)
    path = org.render(template, url=url, title=title, ext=ext)
    path = unique_path(path)
    org.write(path, content)
    return path


__all__ = [
    "FileOrganizer",
    "sanitize_filename",
    "title_to_slug",
    "extract_domain",
    "unique_path",
    "organize_content",
]
