"""Phase 4.4 — CLI 专属测试。

覆盖：
  1. crawagent doctor 基本可运行（exit code 0 或 1 — 都合法）
  2. crawagent add-tool 创建插件包（plugins/<name>/，P2-9 插件规范）+ 重命名 + manifest
  3. crawagent add-tool 拒绝重名
  4. crawagent plugins 列出插件
  5. 没有命令时 argparse 报错
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path


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


def test_add_tool_creates_plugin_scaffold():
    """add-tool 创建 plugins/<name>/ 插件包：manifest + tools/ + skills/ 齐备。"""
    template_dir = PROJECT_ROOT / "crawagent" / "tools" / "_template"
    assert template_dir.exists(), "_template 目录必须存在才能测试 add-tool"

    new_name = "test_scaffold_xyz"
    plugins_root = PROJECT_ROOT / "plugins"
    dst = plugins_root / new_name

    if dst.exists():
        shutil.rmtree(dst, ignore_errors=True)

    try:
        r = _run_cli("add-tool", new_name)
        assert r.returncode == 0, (
            f"add-tool exit={r.returncode}\nstdout={r.stdout}\nstderr={r.stderr}"
        )
        assert dst.exists(), "插件目录应被创建在 plugins/ 下"

        # manifest 存在且 name 已落成插件名
        manifest_path = dst / "plugin.json"
        assert manifest_path.exists(), "plugin.json manifest 应存在"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert manifest["name"] == new_name, "manifest.name 应替换成插件名"

        # 工具模块重命名 + 内容替换
        tool_file = dst / "tools" / f"{new_name}_tool.py"
        assert tool_file.exists(), f"tools/{new_name}_tool.py 应被创建"
        assert not (dst / "tools" / "example_tool.py").exists(), "example_tool.py 应已重命名"
        content = tool_file.read_text(encoding="utf-8")
        assert new_name in content, "工具模块内容应包含新插件名"

        # 技能目录重命名
        skill_md = dst / "skills" / f"{new_name}-usage" / "SKILL.md"
        assert skill_md.exists(), "技能 SKILL.md 应随目录重命名保留"
        sk_text = skill_md.read_text(encoding="utf-8")
        assert f"name: {new_name}-usage" in sk_text, "技能 frontmatter name 应同步替换"
    finally:
        shutil.rmtree(dst, ignore_errors=True)


def test_add_tool_rejects_duplicate():
    """plugins/ 下已有同名目录时拒绝。"""
    plugins_root = PROJECT_ROOT / "plugins"
    plugins_root.mkdir(exist_ok=True)
    fake_name = "dup_check_xyz"
    fake_dst = plugins_root / fake_name
    fake_dst.mkdir(exist_ok=True)

    try:
        r = _run_cli("add-tool", fake_name)
        assert r.returncode == 2, f"重名应 exit=2, 实际 exit={r.returncode}"
        assert "已存在" in r.stdout or "exists" in r.stdout.lower()
    finally:
        shutil.rmtree(fake_dst, ignore_errors=True)


def test_add_plugin_from_local_path():
    """add-plugin 从本地目录接入：校验 manifest、复制进 plugins/。"""
    template_dir = PROJECT_ROOT / "crawagent" / "tools" / "_template"
    plugins_root = PROJECT_ROOT / "plugins"
    fake_src = plugins_root / "_fake_src_plugin"
    dst = plugins_root / "fake-src-plugin"

    shutil.rmtree(fake_src, ignore_errors=True)
    shutil.rmtree(dst, ignore_errors=True)
    shutil.copytree(template_dir, fake_src)
    # manifest name 决定安装目录名
    (fake_src / "plugin.json").write_text(
        json.dumps({"name": "fake-src-plugin", "version": "0.1.0", "dependencies": []}),
        encoding="utf-8",
    )

    try:
        r = _run_cli("add-plugin", str(fake_src))
        assert r.returncode == 0, (
            f"add-plugin exit={r.returncode}\nstdout={r.stdout}\nstderr={r.stderr}"
        )
        assert dst.exists(), "插件应安装到 plugins/fake-src-plugin/"
        assert (dst / "plugin.json").exists()
    finally:
        shutil.rmtree(fake_src, ignore_errors=True)
        shutil.rmtree(dst, ignore_errors=True)


def test_add_plugin_rejects_missing_manifest():
    """add-plugin 拒绝没有 plugin.json 的目录。"""
    plugins_root = PROJECT_ROOT / "plugins"
    fake_src = plugins_root / "_fake_no_manifest"
    shutil.rmtree(fake_src, ignore_errors=True)
    fake_src.mkdir(parents=True)

    try:
        r = _run_cli("add-plugin", str(fake_src))
        assert r.returncode == 2, f"缺 manifest 应 exit=2, 实际 exit={r.returncode}"
        assert "plugin.json" in r.stdout
    finally:
        shutil.rmtree(fake_src, ignore_errors=True)


def test_plugins_lists_installed():
    """plugins 子命令列出 example-rss 示例插件。"""
    r = _run_cli("plugins")
    assert r.returncode == 0, f"plugins exit={r.returncode}, stderr={r.stderr}"
    assert "example-rss" in r.stdout, "示例插件应出现在列表里"


def test_no_command_errors():
    """没子命令时 argparse 应报错（非 0）。"""
    r = _run_cli()
    assert r.returncode != 0, "无命令应 exit != 0"
