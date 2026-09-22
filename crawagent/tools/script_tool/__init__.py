"""script_tool 包 —— 自定义脚本执行工具。

从单文件 script_tool.py 拆分为包（027），外部 import 路径不变：
    from crawagent.tools.script_tool import run_custom_script  # 仍可用
    from crawagent.tools.script_tool import _ensure_tmp, DATA_TMP_DIR  # 仍可用
"""
import subprocess  # re-export for test monkeypatch compatibility（tests do script_tool.subprocess）

from crawagent.config.settings import get_settings
from crawagent.tools.session_dir import session_subdir
from .header import _ALLOWED_HEADER
from .runner import run_custom_script, _ensure_tmp, DATA_TMP_DIR

__all__ = [
    "run_custom_script",
    "_ensure_tmp",
    "DATA_TMP_DIR",
    "get_settings",
    "_ALLOWED_HEADER",
    "session_subdir",
]
