"""附件上传 API（变更 036 —— 激活「添加附件」按钮）。

POST /api/uploads（multipart）：file + session_id。
落盘 data/uploads/<session_id>/<清洗后文件名>——附件是输入材料不是产物，
不进 output/downloads（不参与保留清理策略），与 ADR-0001（背景图存 data/）
同一保管逻辑。

文件名清洗：非法字符转 _、限长（保扩展名）；重名自动追加 -1、-2 序号防覆盖；
目录自动创建。路径安全：落点恒在 data/uploads 内校验（is_relative_to），
防文件名构造逃逸。大小上限 50MB。

返回：
    {ok: true, name: "<落盘名>", path: "<服务端绝对路径>", size: <字节>}  成功
    {ok: false, error: "<人话>"}   超限 / 非法输入 / 落盘失败
"""
from __future__ import annotations

import re

from fastapi import APIRouter, File, Form, UploadFile

from crawagent.config.settings import get_settings
from crawagent.tools.session_dir import _INVALID_CHARS

router = APIRouter()

# 附件大小上限：50MB（输入材料够用；再大该走工作文件夹 + read_file）
MAX_UPLOAD_BYTES = 50 * 1024 * 1024
# 文件名 stem 限长（扩展名另计）
_MAX_STEM = 60


def sanitize_filename(name: str) -> str:
    """清洗上传文件名：取 basename、非法字符转 _、限长保扩展名、空则兜底名。

    与 sanitize_dirname 的差别：必须保留扩展名（AI 靠它判断 pdf/docx 走
    markitdown_convert），且不 strip 点（隐藏文件 .gitignore 合法）。
    """
    base = (name or "").replace("\\", "/").rsplit("/", 1)[-1].strip()
    cleaned = _INVALID_CHARS.sub("_", base).strip()
    if len(cleaned) > _MAX_STEM:
        stem, dot, ext = cleaned.rpartition(".")
        if dot and len(ext) <= 10:
            cleaned = stem[:_MAX_STEM] + "." + ext
        else:
            cleaned = cleaned[:_MAX_STEM]
    return cleaned or "attachment"


def _unique_target(directory, filename: str):
    """重名自动加序号 -1、-2（防覆盖）；返回未占用的落盘 Path。"""
    target = directory / filename
    if not target.exists():
        return target
    stem, dot, ext = filename.rpartition(".")
    prefix, suffix = (stem, "." + ext) if dot and ext else (filename, "")
    for i in range(1, 1000):
        candidate = directory / f"{prefix}-{i}{suffix}"
        if not candidate.exists():
            return candidate
    return target  # 理论到不了：1000 个重名直接覆盖最后一个


def _uploads_root():
    """data/uploads 根目录（每次现读 get_settings，产物目录保存即生效同款）。"""
    return get_settings().project_root / "data" / "uploads"


@router.post("/api/uploads")
async def upload_attachment(
    file: UploadFile = File(...),
    session_id: str = Form(""),
) -> dict:
    """接收一个附件，落盘 data/uploads/<session_id>/ 并返回服务端绝对路径。"""
    sid = (session_id or "").strip()
    if not sid or len(sid) > 128 or re.search(r"[^\w.-]", sid):
        return {"ok": False, "error": "会话 ID 非法"}
    if not file.filename:
        return {"ok": False, "error": "缺少文件名"}

    root = _uploads_root().resolve()
    directory = (root / sid).resolve()
    # 落点恒在 data/uploads 内（sid 已清洗，双保险防构造逃逸）
    if not directory.is_relative_to(root):
        return {"ok": False, "error": "落点非法"}

    target = _unique_target(directory, sanitize_filename(file.filename))
    size = 0
    try:
        directory.mkdir(parents=True, exist_ok=True)
        with target.open("wb") as f:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    f.close()
                    target.unlink(missing_ok=True)
                    return {"ok": False, "error": "文件超过 50MB 上限，请压缩后再传或放进工作文件夹让 AI 直接读"}
                f.write(chunk)
    except Exception as e:
        target.unlink(missing_ok=True)
        return {"ok": False, "error": f"保存失败：{e}"}
    finally:
        await file.close()

    return {"ok": True, "name": target.name, "path": str(target), "size": size}
