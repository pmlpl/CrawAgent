"""文件保存工具 — 将内容写入本地文件（md/txt/json 等）"""
from pathlib import Path
from langchain_core.tools import tool
from crawagent.config.settings import get_settings


@tool
def save_to_file(filename: str, content: str, subdir: str = "output") -> str:
    """Save text content to a local file. Supports markdown, plain text, json, etc.

    Args:
        filename: The output filename, e.g. "chapter1.md" or "result.json"
        content: The text content to write
        subdir: Optional subdirectory under the project root (default: "output")

    Returns:
        On success: "Saved to <absolute file path>, <N> chars written."
        On failure: "Save failed: <error details>".
    """
    try:
        settings = get_settings()
        base = settings.project_root.resolve()
        file_path = (base / subdir / filename).resolve()
        if not file_path.is_relative_to(base):
            return "Save failed: path escapes project root"
        file_path.parent.mkdir(parents=True, exist_ok=True)

        file_path.write_text(content, encoding="utf-8")
        return f"Saved to {file_path}, {len(content)} chars written."
    except Exception as e:
        return f"Save failed: {e}"
