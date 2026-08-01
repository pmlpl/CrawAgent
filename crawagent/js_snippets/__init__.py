"""JS 注入片段管理

提供 Playwright 浏览器渲染时注入的 JS 脚本：
- navigator_overrider: 覆盖 navigator 属性，避免自动化检测
- remove_overlay: 移除弹窗/遮罩层，暴露正文内容
"""
from __future__ import annotations

from pathlib import Path

_SNIPPETS_DIR = Path(__file__).parent

# 缓存
_cache: dict[str, str] = {}


def load_snippet(name: str) -> str:
    """加载 JS 片段

    Args:
        name: 片段名（不含 .js 后缀），如 "navigator_overrider"

    Returns:
        JS 代码字符串
    """
    if name in _cache:
        return _cache[name]

    path = _SNIPPETS_DIR / f"{name}.js"
    if not path.exists():
        raise FileNotFoundError(f"JS snippet not found: {name}")

    code = path.read_text(encoding="utf-8")
    _cache[name] = code
    return code


def get_navigator_overrider() -> str:
    """获取 navigator 属性覆盖脚本"""
    return load_snippet("navigator_overrider")


def get_remove_overlay() -> str:
    """获取弹窗/遮罩移除脚本"""
    return load_snippet("remove_overlay")


def get_all_snippets() -> str:
    """获取所有注入脚本（按顺序拼接）"""
    return "\n\n".join([
        get_navigator_overrider(),
        get_remove_overlay(),
    ])
