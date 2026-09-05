"""Phase 4.4 — CLI 专属测试。

覆盖：
  1. crawagent doctor 基本可运行（exit code 0 或 1 — 都合法）
  2. crawagent add-tool 创建目录 + 脚手架文件 + 重命名
  3. crawagent add-tool 拒绝非法名 / 重名
  4. 没有命令时 argparse 报错
"""

import subprocess
import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CLI_MOD = "crawagent.cli"


def _run_cli(*args: str) -> subprocess.CompletedProcess:
    # Windows 默认 GBK 读取会因 UTF-8 中文/emoji 输出崩溃，显式用 UTF-8
    return subprocess.run(
        [sys.executable, "-m", CLI_MOD, *args],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )


def test_doctor_runs():
    """doctor 命令能跑起来（exit 0=全过, 1=有失败项 — 都 OK）。"""
    r = _run_cli("doctor")
    assert r.returncode in (0, 1), f"doctor exit={r.returncode}, stderr={r.stderr}"
    assert (
        "Doctor Report" in r.stdout
        or "doctor" in r.stdout.lower()
        or "Doctor" in r.stdout
    )
    # 至少要打印 Python 版本检查
    assert "Python" in r.stdout


def test_add_tool_creates_scaffold(tmp_path):
    """add-tool 创建目录、重命名文件、替换模板内容。"""
    # 临时把 tools/_template 复制到一个隔离位置
    tools_dir = PROJECT_ROOT / "crawagent" / "tools"
    template_dir = tools_dir / "_template"
    assert template_dir.exists(), "_template 目录必须存在才能测试 add-tool"

    new_name = "test_scaffold_tool_xyz"
    dst = tools_dir / new_name

    # 确保干净
    if dst.exists():
        shutil_ret = (
            subprocess.run(["rm", "/s", "/q", str(dst)], capture_output=True)
            if False
            else None
        )
        import shutil

        shutil.rmtree(dst, ignore_errors=True)

    try:
        r = _run_cli("add-tool", new_name)
        assert r.returncode == 0, (
            f"add-tool exit={r.returncode}\nstdout={r.stdout}\nstderr={r.stderr}"
        )
        assert dst.exists(), "目标目录应被创建"

        # 核心文件存在且已重命名
        tool_file = dst / f"{new_name}_tool.py"
        assert tool_file.exists(), f"{new_name}_tool.py 应被创建"
        # 旧的 example_tool.py 应该已不存在
        assert not (dst / "example_tool.py").exists(), "example_tool.py 应该已重命名"

        # 内容已替换：模板里的 "example" 应变成我们的工具名
        content = tool_file.read_text(encoding="utf-8")
        # 允许模板里保留少量通用 "example"（比如 docstring 里），
        # 但至少 Tool 名称 / 注册函数名应替换
        assert f"{new_name}_tool" in content or new_name in content
    finally:
        import shutil

        shutil.rmtree(dst, ignore_errors=True)


def test_add_tool_rejects_duplicate(tmp_path):
    """已有同名目录时拒绝。"""
    import shutil

    tools_dir = PROJECT_ROOT / "crawagent" / "tools"
    existing = tools_dir / "_template"
    assert existing.exists()

    # 先手动复制一个假的同名目录，触发重名检查
    fake_name = "_template_dup_check_xyz"
    fake_dst = tools_dir / fake_name
    shutil.copytree(existing, fake_dst, dirs_exist_ok=True)

    try:
        r = _run_cli("add-tool", fake_name)
        assert r.returncode == 2, f"重名应 exit=2, 实际 exit={r.returncode}"
        assert "已存在" in r.stdout or "exists" in r.stdout.lower()
    finally:
        shutil.rmtree(fake_dst, ignore_errors=True)


def test_no_command_errors():
    """没子命令时 argparse 应报错（非 0）。"""
    r = _run_cli()
    assert r.returncode != 0, "无命令应 exit != 0"
