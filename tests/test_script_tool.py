"""script_tool.run_custom_script 测试 — 沙箱执行 + timeout + cwd + 输出截断。

全部离线：mock subprocess.run 和 get_settings，不真起 venv / 不真 fork。
"""
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from crawagent.tools import script_tool


# ---------------------------------------------------------------------------
# fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def fake_settings(tmp_path, monkeypatch):
    """project_root 指 tmp_path，scripts_dir 跟着在 tmp 下。"""
    class _S:
        project_root = tmp_path
        output_dir = tmp_path / "output"
        downloads_dir = tmp_path / "downloads"

    (tmp_path / "data" / "scripts").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(script_tool, "get_settings", lambda: _S())
    return _S


def _fake_completed(stdout="", stderr="", returncode=0):
    """造一个 CompletedProcess 替身，subprocess.run 的返回值。"""
    cp = MagicMock(spec=subprocess.CompletedProcess)
    cp.stdout = stdout
    cp.stderr = stderr
    cp.returncode = returncode
    return cp


# ---------------------------------------------------------------------------
# run_custom_script 基础
# ---------------------------------------------------------------------------

def test_run_basic_print(fake_settings, monkeypatch):
    """最简单脚本：print(1+1) → 返回 "2"。"""
    monkeypatch.setattr(
        script_tool.subprocess, "run",
        lambda *a, **kw: _fake_completed(stdout="2\n"),
    )
    out = script_tool.run_custom_script.func("print(1+1)")
    assert "2" in out
    assert "EXIT CODE" not in out


def test_run_returns_stdout(fake_settings, monkeypatch):
    monkeypatch.setattr(
        script_tool.subprocess, "run",
        lambda *a, **kw: _fake_completed(stdout="hello world"),
    )
    out = script_tool.run_custom_script.func("print('hello world')")
    assert "hello world" in out


def test_run_captures_stderr(fake_settings, monkeypatch):
    """stderr 被收集到结果（[STDERR] 前缀）。"""
    monkeypatch.setattr(
        script_tool.subprocess, "run",
        lambda *a, **kw: _fake_completed(stdout="", stderr="boom"),
    )
    out = script_tool.run_custom_script.func("import sys; sys.stderr.write('boom')")
    assert "[STDERR]" in out
    assert "boom" in out


def test_run_nonzero_exit_marks_error(fake_settings, monkeypatch):
    """returncode != 0 → 输出带 [EXIT CODE N] 前缀。"""
    monkeypatch.setattr(
        script_tool.subprocess, "run",
        lambda *a, **kw: _fake_completed(stdout="partial", returncode=1),
    )
    out = script_tool.run_custom_script.func("raise SystemExit(1)")
    assert "[EXIT CODE 1]" in out


def test_run_timeout_clamped(fake_settings, monkeypatch):
    """timeout < 5 → 抬到 5；timeout > 600 → 压到 600。"""
    seen_timeout = []
    def fake_run(*a, **kw):
        seen_timeout.append(kw.get("timeout"))
        return _fake_completed(stdout="ok")
    monkeypatch.setattr(script_tool.subprocess, "run", fake_run)

    script_tool.run_custom_script.func("pass", timeout=1)   # 抬到 5
    script_tool.run_custom_script.func("pass", timeout=99999)  # 压到 600
    assert 5 in seen_timeout
    assert 600 in seen_timeout


def test_run_subprocess_invoked(fake_settings, monkeypatch):
    """subprocess.run 真的被调一次，传了 stdin (full_code)。"""
    captured = {}
    def fake_run(*a, **kw):
        captured["args"] = a
        captured["kwargs"] = kw
        return _fake_completed(stdout="")
    monkeypatch.setattr(script_tool.subprocess, "run", fake_run)
    script_tool.run_custom_script.func("print(42)")
    assert captured["kwargs"].get("input"), "应通过 stdin 传脚本（方案 A）"
    # cwd 应是 _tmp 目录（脚本内 open() 的文件启动自动删）
    assert "cwd" in captured["kwargs"]
    assert "timeout" in captured["kwargs"]


def test_run_cwd_is_tmp(fake_settings, monkeypatch):
    """脚本执行的 cwd 在 _tmp 目录里。"""
    captured = {}
    monkeypatch.setattr(
        script_tool.subprocess, "run",
        lambda *a, **kw: (captured.setdefault("cwd", kw.get("cwd")) or _fake_completed()),
    )
    script_tool.run_custom_script.func("pass")
    cwd = captured["cwd"]
    assert cwd and "_tmp" in str(cwd), f"cwd 应在 _tmp 下，实际: {cwd}"


def test_run_exception_returns_error_message(fake_settings, monkeypatch):
    """subprocess.run 自身抛错 → 返回错误信息不抛。"""
    def boom(*a, **kw):
        raise RuntimeError("mock subprocess failure")
    monkeypatch.setattr(script_tool.subprocess, "run", boom)
    out = script_tool.run_custom_script.func("anything")
    assert "Error" in out or "mock subprocess failure" in out


def test_run_output_truncated_at_5000(fake_settings, monkeypatch):
    """输出超过 5000 字符 → 截断（保护 token）。"""
    big = "x" * 10000
    monkeypatch.setattr(
        script_tool.subprocess, "run",
        lambda *a, **kw: _fake_completed(stdout=big),
    )
    out = script_tool.run_custom_script.func("print('x'*10000)")
    # 实际截断阈值在 script_tool 内部，看 docstring 说"前 5000 字"
    assert len(out) < len(big), "应被截断"
    assert big[:1000] in out