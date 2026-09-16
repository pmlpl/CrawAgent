"""read_skill 工具测试 — tmp_path 建假 skill 目录，测查找/读取/路径逃逸防护。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from crawagent.graph import skills
from crawagent.graph.skills import read_skill


def _make_skill(tmp_path, name="myskill", body="---\nname: myskill\ndescription: test\n---\n# My Skill\nbody text"):
    d = tmp_path / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(body, encoding="utf-8")
    return d


def test_read_skill_not_found(monkeypatch):
    monkeypatch.setattr(skills, "_parse_skill_dirs", lambda: [])
    r = read_skill.func("nonexistent")
    assert "SKILL_NOT_FOUND" in r


def test_read_skill_normal(tmp_path, monkeypatch):
    _make_skill(tmp_path)
    monkeypatch.setattr(skills, "_parse_skill_dirs", lambda: [tmp_path])
    r = read_skill.func("myskill")
    assert "body text" in r


def test_read_skill_ref_file(tmp_path, monkeypatch):
    d = _make_skill(tmp_path)
    refs = d / "references"
    refs.mkdir()
    (refs / "cookbook.md").write_text("cookbook content here", encoding="utf-8")
    monkeypatch.setattr(skills, "_parse_skill_dirs", lambda: [tmp_path])
    r = read_skill.func("myskill", ref="references/cookbook.md")
    assert "cookbook content" in r


def test_read_skill_missing_file(tmp_path, monkeypatch):
    _make_skill(tmp_path)
    monkeypatch.setattr(skills, "_parse_skill_dirs", lambda: [tmp_path])
    r = read_skill.func("myskill", ref="references/nope.md")
    assert "SKILL_NOT_FOUND" in r and "file not found" in r


def test_read_skill_ref_escape_blocked(tmp_path, monkeypatch):
    """ref 路径逃逸出 skill 目录 → SKILL_ERROR。"""
    _make_skill(tmp_path)
    monkeypatch.setattr(skills, "_parse_skill_dirs", lambda: [tmp_path])
    r = read_skill.func("myskill", ref="../../etc/passwd")
    assert "SKILL_ERROR" in r and "escapes" in r
