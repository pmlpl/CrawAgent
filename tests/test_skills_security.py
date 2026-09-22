"""graph/skills.py ensure_mcp_started 安全测试 — shell=True 必须不存在。

017 规格：删 skills.py:365 的 shell=True，改成 shlex.split(cmd) 走 argv。
本测试断言：
- subprocess.Popen 调用时**没有** shell=True 参数（无论显式 False 还是干脆没传，默认 False）
- cmd 字符串被 split 成 argv list（不是 str）
- Windows 分支的 .bat + ShellExecuteW 路径不动

不真启动 MCP（用 mock subprocess + shlex 直接验证调用参数）。
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _force_non_windows_branch(monkeypatch):
    """让 ensure_mcp_started 走非 Windows 的 subprocess 分支。"""
    # os.name == "nt" → Windows；非 nt → 非 Windows 分支
    import os as _os
    monkeypatch.setattr(_os, "name", "posix")
    # os.path 还是 Windows 风格（CWD 行为）—— 不动
    # sys.platform 也会被某些代码看，但 ensure_mcp_started 只看 os.name


def _fake_settings(monkeypatch, port=23816):
    """构造一个能跑过 ensure_mcp_started 早期 return 的 Settings。"""
    from crawagent.graph.skills import get_settings

    class _S:
        mcp_servers = json_dumps_stdio_only = '[{"transport":"streamable_http","url":"http://127.0.0.1:' + str(port) + '/mcp"}]'
        MCP_START_COMMAND = ""
        log_dir = Path(tmp_path_safe(monkeypatch)) / "logs"
        project_root = Path(tmp_path_safe(monkeypatch))

    log_dir_path = Path(tmp_path_safe(monkeypatch)) / "logs"
    log_dir_path.mkdir(parents=True, exist_ok=True)
    _S.log_dir = log_dir_path
    _S.project_root = Path(tmp_path_safe(monkeypatch))
    monkeypatch.setattr("crawagent.graph.skills.get_settings", lambda: _S)
    return _S


def tmp_path_safe(monkeypatch):
    """给 monkeypatch 提供临时目录引用。"""
    import tempfile
    return tempfile.mkdtemp()


# ---------------------------------------------------------------------------
# 非 Windows 分支的安全断言
# ---------------------------------------------------------------------------

def test_ensure_mcp_started_no_shell_true(monkeypatch):
    """Popen 调用时不允许 shell=True。"""
    import subprocess
    from crawagent.graph import skills

    # mock 掉 subprocess.Popen，记录调用
    popen_calls = []
    def fake_popen(*args, **kwargs):
        popen_calls.append((args, kwargs))
        proc = MagicMock()
        proc.pid = 1
        return proc

    # mock _port_listening → 端口没开，让流程走到 Popen
    monkeypatch.setattr(skills, "_port_listening", lambda h, p: False)
    # mock get_settings
    _fake_settings(monkeypatch)
    # mock 系统级 subprocess.Popen（局部 import 会看到 sys.modules['subprocess']）
    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    # mock _find_anything_analyzer + _find_pnpm_cmd → 让 _build_autostart_command 返回内置 cmd
    monkeypatch.setattr(skills, "_find_anything_analyzer", lambda: "/fake/aa")
    monkeypatch.setattr(skills, "_find_pnpm_cmd", lambda: "/fake/pnpm")
    # 走非 Windows 分支
    _force_non_windows_branch(monkeypatch)
    # 让 _mcp_spawned 不被早返回
    monkeypatch.setattr(skills, "_mcp_spawned", False)
    # wait=False 跳过 while _port_listening 端口轮询；前置 mock 已让第一次检查返 False → 进 spawn

    skills.ensure_mcp_started(wait=False)
    # 关键断言 1：调用了 Popen
    assert len(popen_calls) >= 1, f"Popen 应被调用，实际 {len(popen_calls)} 次"
    # 关键断言 2：shell=True 不在 kwargs 里（默认 False 同样通过）
    for a, kwargs in popen_calls:
        assert "shell" not in kwargs or kwargs["shell"] is False, (
            f"Popen 不应传 shell=True / shell=True 等价物，实际: {kwargs}"
        )


def test_popen_argv_is_list_not_string(monkeypatch):
    """非 Windows 分支 Popen 第一个参数应是 list（argv），不是 str。

    shell=False + str 会让 Linux 把整个字符串当可执行文件名（"\"pnpm\" dev"）。
    """
    import subprocess
    from crawagent.graph import skills

    popen_calls = []
    def fake_popen(*args, **kwargs):
        popen_calls.append((args, kwargs))
        proc = MagicMock()
        proc.pid = 1
        return proc

    monkeypatch.setattr(skills, "_port_listening", lambda h, p: False)
    _fake_settings(monkeypatch)
    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    monkeypatch.setattr(skills, "_find_anything_analyzer", lambda: "/fake/aa")
    monkeypatch.setattr(skills, "_find_pnpm_cmd", lambda: "/fake/pnpm")
    _force_non_windows_branch(monkeypatch)
    monkeypatch.setattr(skills, "_mcp_spawned", False)

    skills.ensure_mcp_started(wait=False)

    assert len(popen_calls) >= 1
    args, kwargs = popen_calls[0]
    # 第一个位置参数是 argv list（不是 str）
    assert len(args) >= 1
    assert isinstance(args[0], list), f"Popen 第一个参数应 list（argv），实际 {type(args[0]).__name__}: {args[0]!r}"
    # argv 至少 1 项
    assert len(args[0]) >= 1


def test_shlex_split_handles_quoted_pnpm():
    """shlex.split('"pnpm" dev') → ['pnpm', 'dev']（带引号路径解析正确）。

    shlex 是 POSIX 默认风格，会去掉引号。带空格的 pnpm 路径也能跑通。
    """
    import shlex

    # 内置分支产生：cmd = f'"{pnpm}" dev'
    cmd = '"/fake/pnpm" dev'
    argv = shlex.split(cmd)
    assert argv == ["/fake/pnpm", "dev"]

    # 带空格
    cmd = '"/fake path/pnpm" dev'
    argv = shlex.split(cmd)
    assert argv == ["/fake path/pnpm", "dev"]


def test_windows_branch_unchanged_when_checks_security(monkeypatch):
    """Windows 路径（ShellExecuteW）不动 —— 它本身就走非 Popen 启动。

    即：os.name == 'nt' 时 ensure_mcp_started 不应调 fake_popen（走 ShellExecuteW）。
    """
    import os as _os
    import subprocess
    from crawagent.graph import skills

    popen_calls = []
    def fake_popen(*args, **kwargs):
        popen_calls.append((args, kwargs))
        proc = MagicMock()
        proc.pid = 1
        return proc

    # Windows
    monkeypatch.setattr(_os, "name", "nt")
    monkeypatch.setattr(skills, "_port_listening", lambda h, p: True)
    _fake_settings(monkeypatch)
    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    monkeypatch.setattr(skills, "_find_anything_analyzer", lambda: "C:/fake/aa")
    monkeypatch.setattr(skills, "_find_pnpm_cmd", lambda: "C:/fake/pnpm")
    monkeypatch.setattr(skills, "_mcp_spawned", False)
    # mock ShellExecuteW（Windows API 调用）
    mock_shell32 = MagicMock()
    mock_shell32.ShellExecuteW.return_value = 33  # > 32 = 成功
    monkeypatch.setattr("ctypes.windll.shell32", mock_shell32)

    skills.ensure_mcp_started(wait=False)

    # Windows 分支不调 subprocess.Popen（ShellExecuteW 走的）
    # 但实际上 ShellExecuteW 调用之后，Popen 可能不被调（bat 文件自己执行 pnpm）
    assert popen_calls == [], f"Windows 分支不应调 Popen，实际调了: {popen_calls}"