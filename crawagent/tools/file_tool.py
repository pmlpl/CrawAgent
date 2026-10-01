"""文件读写工具 — save_to_file（写入）/ read_file（读取，变更 036 附件配套）

save_to_file 增强特性：
- overwrite / append 两种写入模式
- 自动检测 JSON 字符串并 pretty-print
- 自动补扩展名（无后缀 + JSON 内容 → .json）
- 路径安全校验（防目录逃逸）
- 返回文件位置信息（供后续工具引用）

read_file（036）：读本地文本文件，offset/limit 分段（大文件多轮读全）；
二进制内容提示改走 markitdown_convert。读任意路径是有意设计——与
run_custom_script 能力面等价，不新增攻击面；work_dir 逃逸校验只管写工具。
"""
from __future__ import annotations

import json
from pathlib import Path

from langchain_core.tools import tool

from crawagent.config.settings import get_settings


def _resolve_file_path(filename: str, subdir: str) -> Path:
    """安全地把 filename + subdir 解析为绝对路径。

    变更 034：会话配置了工作文件夹（work_dir）时以它为基准——
    subdir 空（或 "output"）直接落 work_dir 根，否则落 work_dir/<subdir>/，
    不再套 014 的会话子目录；路径安全校验基准同步改为 work_dir
    （is_relative_to(work_dir)），用户选的文件夹就是边界，逃逸即拒绝。
    未配置 work_dir：行为与改前一致——subdir 空默认落当前会话子目录
    （014），无会话上下文（CLI/worker/直调工具）落 output/ 根。

    返回值保证在对应基准目录内；若路径逃逸则抛 ValueError。
    """
    settings = get_settings()
    base = settings.project_root.resolve()
    output_root = settings.output_dir.resolve()

    from crawagent.tools.session_dir import current_work_dir
    work_dir = current_work_dir()
    if work_dir:
        work_root = Path(work_dir).resolve()
        subdir_clean = subdir.strip("/\\")
        if subdir_clean in ("output", ""):
            file_path = (work_root / filename).resolve()
        else:
            file_path = (work_root / subdir_clean / filename).resolve()
        if not Path(file_path).is_relative_to(work_root):
            raise ValueError(f"path escapes work dir: {file_path}")
        return file_path

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
        # 变更 038 路径失效防护：work_dir 已绑定但目录被删 → 人话报错不重建不写入
        from crawagent.tools.session_dir import work_dir_unavailable
        unavailable = work_dir_unavailable()
        if unavailable:
            return f"Save failed: {unavailable}"

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


# read_file 单次返回的字符硬上限（防单次调用撑爆上下文；超限自动截断到它）
READ_FILE_LIMIT_CAP = 50000


@tool
def read_file(path: str, offset: int = 0, limit: int = 20000) -> str:
    """Read a local text file's content (attachments, exported files, logs, code...).

    Parameters:
        path: absolute path of the file (attachments uploaded via the chat UI
            carry their server path in the [附件] block of the user message)
        offset: character position to start reading from (0 = beginning). When a
            previous read says "继续读传 offset=N", pass that N here
        limit: max characters to return this call (default 20000, hard cap 50000)

    Returns:
        The text slice, with a tail hint: "（共 N 字符，已读 a-b，继续读传
        offset=b）" when more content remains, or "（共 N 字符，已全部读完）"
        when done. Binary content (pdf/docx/xlsx etc.) returns a hint to convert
        with markitdown_convert instead. Not-found paths return a plain error.

    Notes:
        - LARGE files: the tool result is truncated in context after ~2000 chars,
          so you often only see head+tail of one call — ALWAYS keep reading with
          the returned offset until the "已全部读完" hint before summarizing.
        - Encoding is utf-8 (errors replaced); undecodable garbage is reported
          as suspected binary rather than returned as mojibake.
    """
    try:
        p = Path(path)
        if not p.is_file():
            return f"Read failed: 文件不存在（或不是常规文件）: {path}"

        with p.open("rb") as f:
            raw_head = f.read(8192)
        if b"\x00" in raw_head:
            return (
                f"疑似二进制文件（含 \\0 字节），直接读会得到乱码：{path}\n"
                "pdf/docx/xlsx/ppt 等请改用 markitdown_convert 转成 Markdown 后再读。"
            )

        text = p.read_text(encoding="utf-8", errors="replace")
        # 替换符占比过高也按二进制处理（utf-8 errors=replace 会把坏字节变 \ufffd）
        if text.count("\ufffd") > max(64, len(text) * 0.3):
            return (
                f"疑似二进制或非 UTF-8 编码文件（解码失败率过高）：{path}\n"
                "pdf/docx 请改用 markitdown_convert；其他编码可让用户转存为 UTF-8。"
            )

        total = len(text)
        if total == 0:
            return "（共 0 字符，文件为空）"
        offset = max(0, int(offset or 0))
        limit = min(max(1, int(limit or 0)), READ_FILE_LIMIT_CAP)
        if offset >= total:
            return f"（共 {total} 字符，offset 已超出文件末尾，没有更多内容）"

        end = min(offset + limit, total)
        body = text[offset:end]
        if end < total:
            return f"{body}\n（共 {total} 字符，已读 {offset}-{end}，继续读传 offset={end}）"
        return f"{body}\n（共 {total} 字符，已读 {offset}-{end}，已全部读完）"
    except Exception as e:
        return f"Read failed: {e}"
