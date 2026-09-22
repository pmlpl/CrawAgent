"""site_analyze_tool 测试 — Playwright 真集成成本高，只覆盖错误路径 + JSON 输出。

完整路径需真实浏览器，超出单测成本；改由 e2e 测试覆盖。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from crawagent.tools import site_analyze_tool


def test_analyze_site_structure_playwright_failure_returns_error_json():
    """Playwright 启动失败 → 返回 error JSON。"""
    def fake_run(coro):
        coro.close()
        raise RuntimeError("chromium not installed")
    with patch.object(site_analyze_tool.asyncio, "run", side_effect=fake_run):
        out = site_analyze_tool.analyze_site_structure.func("https://example.com/page")
    data = json.loads(out)
    assert "chromium not installed" in data["error"]


def test_analyze_site_structure_returns_valid_json_on_unexpected_error():
    """任何异常都被包装为 JSON（保证 LLM 能解析）。"""
    def fake_run(coro):
        coro.close()
        raise ValueError("unexpected")
    with patch.object(site_analyze_tool.asyncio, "run", side_effect=fake_run):
        out = site_analyze_tool.analyze_site_structure.func("https://example.com/")
    # JSON 合法
    data = json.loads(out)
    assert isinstance(data, dict)
    assert "error" in data