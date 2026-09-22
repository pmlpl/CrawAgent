"""file_tool 测试 — 路径安全 / 后缀推断 / JSON / 子目录。

依赖测试约定（pytest tmp_path）：mock get_settings 让 output_dir 指到 tmp_path，
所有写入都进 tmp，绝不污染 data/output/。
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from crawagent.tools import file_tool


# ---------------------------------------------------------------------------
# fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def fake_settings(tmp_path, monkeypatch):
    """把 file_tool.get_settings() 指到 tmp_path（与 test_session_dir.py 同款）。"""
    class _S:
        project_root = tmp_path
        output_dir = tmp_path / "output"

    (tmp_path / "output").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(file_tool, "get_settings", lambda: _S())
    return _S


# ---------------------------------------------------------------------------
# save_to_file 基础 + 边界
# ---------------------------------------------------------------------------

def test_save_basic_md(fake_settings, tmp_path):
    out = file_tool.save_to_file.func("x.md", "hello", subdir="d")
    p = tmp_path / "output" / "d" / "x.md"
    assert p.read_text(encoding="utf-8") == "hello"
    assert "Saved to" in out


def test_save_auto_ext_json_from_content(fake_settings, tmp_path):
    """filename 无后缀 + JSON 内容 → 自动加 .json"""
    file_tool.save_to_file.func("data", '{"a":1}', subdir="d")
    assert (tmp_path / "output" / "d" / "data.json").exists()


def test_save_auto_ext_txt_from_text(fake_settings, tmp_path):
    """filename 无后缀 + 纯文本 → 自动加 .txt"""
    file_tool.save_to_file.func("notes", "hello world", subdir="d")
    assert (tmp_path / "output" / "d" / "notes.txt").exists()


def test_save_explicit_ext_passthrough(fake_settings, tmp_path):
    """filename 已有 .md → 不再加后缀"""
    file_tool.save_to_file.func("x.md", "hi", subdir="d")
    assert (tmp_path / "output" / "d" / "x.md").exists()
    assert not (tmp_path / "output" / "d" / "x.md.txt").exists()


def test_save_json_pretty_print(fake_settings, tmp_path):
    content = '{"a":1,"b":[1,2,3]}'
    file_tool.save_to_file.func("p.json", content, subdir="d")
    written = (tmp_path / "output" / "d" / "p.json").read_text(encoding="utf-8")
    # 缩进后的多行 JSON
    assert "\n" in written
    assert json.loads(written) == {"a": 1, "b": [1, 2, 3]}


def test_save_non_json_passthrough(fake_settings, tmp_path):
    """非 JSON 内容 → 不格式化，原样写"""
    file_tool.save_to_file.func("a.md", "just text", subdir="d")
    assert (tmp_path / "output" / "d" / "a.md").read_text(encoding="utf-8") == "just text"


def test_save_append_mode(fake_settings, tmp_path):
    file_tool.save_to_file.func("a.md", "line1\n", subdir="d", mode="overwrite")
    file_tool.save_to_file.func("a.md", "line2\n", subdir="d", mode="append")
    written = (tmp_path / "output" / "d" / "a.md").read_text(encoding="utf-8")
    assert "line1" in written and "line2" in written
    # 顺序：append 在后面
    assert written.index("line1") < written.index("line2")


def test_save_overwrite_replaces(fake_settings, tmp_path):
    file_tool.save_to_file.func("a.md", "old", subdir="d")
    file_tool.save_to_file.func("a.md", "new", subdir="d")
    assert (tmp_path / "output" / "d" / "a.md").read_text(encoding="utf-8") == "new"


def test_save_traversal_blocked(fake_settings):
    """filename 含 ../ → 抛 ValueError（路径逃逸被拦截）。"""
    out = file_tool.save_to_file.func("../../../etc/passwd", "x", subdir="d")
    assert "Save failed" in out
    assert "path escapes" in out.lower()


def test_save_session_default_subdir(fake_settings, tmp_path, monkeypatch):
    """subdir="" 且无 session contextvar → 落 output/ 根（014 行为）。"""
    file_tool.save_to_file.func("root.md", "root content", subdir="")
    assert (tmp_path / "output" / "root.md").exists()


def test_save_invalid_filename_in_too_far(fake_settings):
    """filename 含非法 Windows 字符 — save_to_file 当前依赖 pathlib，应该报错而不是写出错位置的文件。"""
    out = file_tool.save_to_file.func("a<b>.md", "x", subdir="d")
    # 当前实现：pathlib 会拒绝这种文件名，抛 OSError，被 except 捕获 → "Save failed"
    assert "Save failed" in out


def test_path_prefix_confusion_blocked(tmp_path, monkeypatch):
    """prefix confusion 攻击：路径 ``<base>XYZ/foo`` 以 ``<base>`` 开头但不在 ``<base>`` 下。

    经典 startswith 漏判场景：``tmp_path/myroot_evil/x.md``.startswith(``tmp_path/myroot``) == True
    但 ``tmp_path/myroot_evil/x.md``.is_relative_to(``tmp_path/myroot``) == False。

    模拟 symlink 攻击的等价行为（避免 Windows 创建 symlink 的权限问题）：
    monkeypatch Path.resolve 把所有 ``myroot/output/d`` 解析到 ``myroot_evil/output/d``，
    base 不变。startswith 实现漏判（测试 red），is_relative_to 实现正确拦截（测试 green）。

    见 升级改动文档/019-file-tool-path-resolve.md §3.1。
    """
    class _S:
        project_root = tmp_path / "myroot"
        output_dir = tmp_path / "myroot" / "output"

    (tmp_path / "myroot" / "output").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(file_tool, "get_settings", lambda: _S())

    from pathlib import Path
    real_resolve = Path.resolve

    def fake_resolve(self, *args, **kwargs):
        s = str(self)
        # 把所有 "myroot/output/d" 类相对路径解析到 myroot_evil/output/d
        # base = myroot 不变，但 file_path 解析到 myroot_evil（base 的"兄弟"目录）
        if "myroot" in s and "output" in s:
            return Path(s.replace("myroot", "myroot_evil"))
        return real_resolve(self, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", fake_resolve)

    # 子目录 "d" 解析到 myroot_evil/output/d，base = myroot → 应当被拦截
    out = file_tool.save_to_file.func("x.md", "evil", subdir="d")
    assert "Save failed" in out
    assert "path escapes" in out.lower()
    # 确认文件没写到 base 外
    assert not (tmp_path / "myroot_evil").exists()