"""tests/test_utils.py - UserAgentPool 和导出函数测试"""
import json
import csv
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from crawagent.tools.utils import (
    UserAgentPool,
    export_json,
    export_csv,
    export_markdown,
)


class TestUserAgentPool:
    """UserAgentPool 测试"""

    def test_pick_returns_valid_ua(self):
        pool = UserAgentPool()
        ua = pool.pick()
        assert isinstance(ua, str)
        assert len(ua) > 0
        assert "Mozilla" in ua

    def test_pick_never_returns_none(self):
        pool = UserAgentPool()
        for _ in range(100):
            ua = pool.pick()
            assert ua is not None
            assert isinstance(ua, str)

    def test_pick_avoids_immediate_repeat(self):
        pool = UserAgentPool()
        if len(pool.pool) > 1:
            first = pool.pick()
            second = pool.pick()
            assert first != second

    def test_add_AddsNewUA(self):
        pool = UserAgentPool(pool=[])
        assert len(pool.pool) == 0
        pool.add("TestBrowser/1.0")
        assert "TestBrowser/1.0" in pool.pool

    def test_add_IgnoresDuplicates(self):
        pool = UserAgentPool(pool=["Test/1.0"])
        pool.add("Test/1.0")
        assert pool.pool.count("Test/1.0") == 1


class TestExportJson:
    """export_json 测试"""

    def test_export_dict(self):
        data = {"name": "test", "value": 123}
        with TemporaryDirectory() as tmpdir:
            path = export_json(data, Path(tmpdir) / "test.json")
            assert path.exists()
            loaded = json.loads(path.read_text(encoding="utf-8"))
            assert loaded == data

    def test_export_list(self):
        data = [{"a": 1}, {"b": 2}]
        with TemporaryDirectory() as tmpdir:
            path = export_json(data, Path(tmpdir) / "test.json")
            loaded = json.loads(path.read_text(encoding="utf-8"))
            assert loaded == data

    def test_creates_parent_dirs(self):
        data = {"key": "value"}
        with TemporaryDirectory() as tmpdir:
            path = export_json(data, Path(tmpdir) / "subdir" / "nested" / "test.json")
            assert path.exists()
            assert path.parent.exists()


class TestExportCsv:
    """export_csv 测试"""

    def test_export_basic(self):
        data = [{"name": "Alice", "age": 30}, {"name": "Bob", "age": 25}]
        with TemporaryDirectory() as tmpdir:
            path = export_csv(data, Path(tmpdir) / "test.csv")
            assert path.exists()
            text = path.read_text(encoding="utf-8-sig")
            reader = csv.DictReader(text.splitlines())
            rows = list(reader)
            assert len(rows) == 2
            assert rows[0]["name"] == "Alice"

    def test_export_empty(self):
        data = []
        with TemporaryDirectory() as tmpdir:
            path = export_csv(data, Path(tmpdir) / "empty.csv")
            assert path.exists()
            assert path.read_text(encoding="utf-8") == ""


class TestExportMarkdown:
    """export_markdown 测试"""

    def test_export_basic(self):
        data = [{"name": "Alice", "score": 100}, {"name": "Bob", "score": 90}]
        with TemporaryDirectory() as tmpdir:
            path = export_markdown(data, "Leaderboard", Path(tmpdir) / "test.md")
            assert path.exists()
            text = path.read_text(encoding="utf-8")
            assert "# Leaderboard" in text
            assert "| name | score |" in text
            assert "| Alice | 100 |" in text

    def test_export_empty(self):
        data = []
        with TemporaryDirectory() as tmpdir:
            path = export_markdown(data, "Empty", Path(tmpdir) / "empty.md")
            text = path.read_text(encoding="utf-8")
            assert "_无数据_" in text
