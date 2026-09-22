"""文件保存工具 — 将内容写入本地文件（md/txt/json 等）

增强特性：
- overwrite / append 两种写入模式
- 自动检测 JSON 字符串并 pretty-print
- 自动补扩展名（无后缀 + JSON 内容 → .json）
- 路径安全校验（防目录逃逸）
- 返回文件位置信息（供后续工具引用）
"""
from __future__ import annotations

import json
from pathlib import Path

from langchain_core.tools import tool

from crawagent.config.settings import get_settings


def _resolve_file_path(filename: str, subdir: str) -> Path:
    """安全地把 filename + subdir 解析为项目内绝对路径。

    返回值保证在 output_dir 内；若路径逃逸则抛 ValueError。
    subdir 为空时默认落当前会话子目录（变更 014 会话级产物目录）；
    无会话上下文（CLI/worker/直调工具）时落 output/ 根，行为与改前一致。
    """
    settings = get_settings()
    base = settings.project_root.resolve()
    output_root = settings.output_dir.resolve()

    subdir_clean = subdir.strip("/\\")
    if not subdir_clean:
        from crawagent.tools.session_dir import session_subdir
        subdir_clean = session_subdir()
    if subdir_clean in ("output", ""):
        file_path = (output_root / filename).resolve()
    else:
        file_path = (output_root / subdir_clean / filename).resolve()

    if not Path(file_path).is_relative_to(base):
        raise ValueError(f"path escapes project root: {file_path}")

    return file_path


def _auto_ext_name(filename: str, content: str) -> str:
    """filename 无扩展名时根据内容特征推断。"""
    if "." in Path(filename).name:
        return filename  # 已有后缀
    stripped = content.strip()
    if (stripped.startswith("{") and stripped.endswith("}")) or \
       (stripped.startswith("[") and stripped.endswith("]")):
        return filename + ".json"
    return filename + ".txt"


def _try_json_load(content: str) -> str:
    """如果 content 是合法 JSON，返回 pretty-printed 版本；否则原样返回。"""
    stripped = content.strip()
    if not stripped:
        return content
    if not ((stripped.startswith("{") and stripped.endswith("}")) or
            (stripped.startswith("[") and stripped.endswith("]"))):
        return content
    try:
        obj = json.loads(stripped)
        return json.dumps(obj, ensure_ascii=False, indent=2)
    except (json.JSONDecodeError, TypeError):
        return content


@tool
def save_to_file(filename: str, content: str, subdir: str = "", mode: str = "overwrite") -> str:
    """保存文本内容到本地文件。支持 md、纯文本、json 等格式。

    特性：
    - 自动检测 JSON 字符串并 pretty-print 缩进
    - filename 无后缀时根据内容自动补（JSON → .json，其他 → .txt）
    - mode="append" 可追加到已有文件
    - 始终在 output_dir 下写入，subdir 控制子目录

    参数：
        filename: 输出文件名，如 "chapter1.md" 或 "result.json"
        content: 要写入的文本内容
        subdir: output_dir 下的子目录（默认 "" 即直接写 output_dir 根）
        mode: "overwrite" (默认，覆盖写) 或 "append" (追加写)

    返回：
        成功："Saved to <绝对路径>, <N> chars written (<overwrite|append>)."
        失败："Save failed: <错误详情>"
    """
    try:
        file_path = _resolve_file_path(filename, subdir)

        # 自动补后缀
        final_name = _auto_ext_name(file_path.name, content)
        if final_name != file_path.name:
            file_path = file_path.with_name(final_name)

        # JSON pretty-print
        final_content = _try_json_load(content)

        file_path.parent.mkdir(parents=True, exist_ok=True)

        write_mode = "w" if mode != "append" else "a"
        with file_path.open(mode=write_mode, encoding="utf-8") as f:
            f.write(final_content)

        return (
            f"Saved to {file_path}, {len(final_content)} chars written ({write_mode})."
        )
    except Exception as e:
        return f"Save failed: {e}"
