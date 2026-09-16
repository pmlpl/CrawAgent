"""recommend_scripts 测试 — tmp_path 建假档案，测匹配评分逻辑（纯字符串，无向量）。"""
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from crawagent.tools import site_profile_tool
from crawagent.tools.site_profile_tool import recommend_scripts


def _setup_profiles(tmp_path, monkeypatch, profiles):
    """把 _profiles_dir 指向 tmp_path，写入给定档案列表。"""
    monkeypatch.setattr(site_profile_tool, "_profiles_dir", lambda: tmp_path)
    monkeypatch.setattr(site_profile_tool, "_ensure_migrated", lambda: None)
    monkeypatch.setattr(site_profile_tool, "_MIGRATION_CHECKED", True)
    for p in profiles:
        fname = site_profile_tool._origin_to_filename(p["origin"])
        (tmp_path / fname).write_text(
            yaml.safe_dump(p, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )


def test_recommend_empty_pool(tmp_path, monkeypatch):
    _setup_profiles(tmp_path, monkeypatch, [])
    r = recommend_scripts.func()
    assert "NO_RECOMMEND" in r and "档案库为空" in r


def test_recommend_by_url_kind_weread(tmp_path, monkeypatch):
    """url 含 weread.qq.com → 推 kind=weread_cookie 的档案。"""
    _setup_profiles(tmp_path, monkeypatch, [
        {"origin": "https://weread.qq.com", "title": "微信读书", "kind": "weread_cookie",
         "strategy": "cookie", "script": "# weread script",
         "last_crawled_at": "2026-09-01T00:00:00"},
        {"origin": "https://other.com", "title": "其他站", "kind": "other",
         "strategy": "x", "last_crawled_at": "2026-09-01T00:00:00"},
    ])
    r = recommend_scripts.func(url="https://weread.qq.com/book/123")
    assert "RECOMMEND" in r
    assert "weread.qq.com" in r
    assert "other.com" not in r


def test_recommend_by_url_kind_csdn(tmp_path, monkeypatch):
    _setup_profiles(tmp_path, monkeypatch, [
        {"origin": "https://blog.csdn.net", "title": "CSDN", "kind": "csdn_blog",
         "strategy": "csdn", "last_crawled_at": "2026-09-01T00:00:00"},
    ])
    r = recommend_scripts.func(url="https://blog.csdn.net/x/article/1")
    assert "RECOMMEND" in r and "csdn" in r.lower()


def test_recommend_by_hint_keyword(tmp_path, monkeypatch):
    """hint 关键词命中 strategy/title 文本。"""
    _setup_profiles(tmp_path, monkeypatch, [
        {"origin": "https://a.com", "title": "A站", "kind": "blog",
         "strategy": "字体混淆解密", "last_crawled_at": "2026-09-01T00:00:00"},
        {"origin": "https://b.com", "title": "B站", "kind": "video",
         "strategy": "m3u8", "last_crawled_at": "2026-09-01T00:00:00"},
    ])
    r = recommend_scripts.func(hint="字体混淆")
    assert "RECOMMEND" in r
    assert "a.com" in r
    assert "b.com" not in r


def test_recommend_script_preview(tmp_path, monkeypatch):
    """带 saved_script 的档案输出 script_preview。"""
    _setup_profiles(tmp_path, monkeypatch, [
        {"origin": "https://a.com", "title": "A", "kind": "x",
         "strategy": "s", "script": "# coding: utf-8\nprint('hi')",
         "last_crawled_at": "2026-09-01T00:00:00"},
    ])
    r = recommend_scripts.func(hint="x")
    assert "script_preview" in r


def test_recommend_no_match(tmp_path, monkeypatch):
    _setup_profiles(tmp_path, monkeypatch, [
        {"origin": "https://a.com", "title": "A", "kind": "blog",
         "strategy": "x", "last_crawled_at": "2026-09-01T00:00:00"},
    ])
    r = recommend_scripts.func(hint="完全不存在的关键词xyz123")
    assert "NO_RECOMMEND" in r


def test_recommend_limit_clamp(tmp_path, monkeypatch):
    """limit 超过 5 取 5。"""
    profiles = []
    for i in range(6):
        profiles.append({
            "origin": f"https://site{i}.com", "title": f"S{i}",
            "kind": "x", "strategy": "common",
            "script": f"# script {i}",
            "last_crawled_at": f"2026-09-0{i+1}T00:00:00",
        })
    _setup_profiles(tmp_path, monkeypatch, profiles)
    r = recommend_scripts.func(hint="common", limit=10)
    assert "5 条" in r  # clamp 到 5
