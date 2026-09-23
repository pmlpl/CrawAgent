"""extract_media：从工具结果提取媒体路径 + 生成 /downloads url（变更 012）。
monkeypatch media.get_settings 返回临时 downloads_dir，不碰真实配置。
"""
import json
import sys
from pathlib import Path

import pytest

from crawagent.web import media as media_mod


@pytest.fixture()
def dl(tmp_path, monkeypatch):
    """临时 downloads_dir：建子目录 + 假文件。"""
    root = tmp_path / "downloads"
    (root / "douyin_abc").mkdir(parents=True)
    (root / "douyin_abc" / "v.mp4").write_bytes(b"fake")
    (root / "douyin_abc" / "cover.jpg").write_bytes(b"img")
    (root / "douyin_abc" / "audio.mp3").write_bytes(b"au")
    fake = type("S", (), {"downloads_dir": root})()
    monkeypatch.setattr(media_mod, "get_settings", lambda: fake)
    return root


def _social_json(*items):
    """构造 download_social_media 同款 JSON（json.dumps 正确转义路径反斜杠）。"""
    return json.dumps({"platform": "douyin", "downloaded": [{"type": t, "path": str(p)} for t, p in items]})


def test_json_format_extracts_media(dl):
    """download_social_media 返 JSON downloaded[]，提取 video/cover(→image)/audio。"""
    content = _social_json(
        ("video", dl / "douyin_abc" / "v.mp4"),
        ("cover", dl / "douyin_abc" / "cover.jpg"),
        ("audio", dl / "douyin_abc" / "audio.mp3"),
    )
    out = media_mod.extract_media(content)
    assert len(out) == 3
    types = {m["type"] for m in out}
    assert types == {"video", "image", "audio"}
    for m in out:
        assert m["url"].startswith("/downloads/douyin_abc/")
        assert m["filename"] in ("v.mp4", "cover.jpg", "audio.mp3")


def test_text_format_extracts_media(dl):
    """download_images 返文本 'output dir: <dir>' + 文件名行。"""
    sep = "\\" if sys.platform == "win32" else "/"
    content = f"downloaded 2 files\noutput dir: {dl}{sep}douyin_abc\n v.mp4 (1.2MB)\n cover.jpg (50KB)"
    out = media_mod.extract_media(content)
    names = {m["filename"] for m in out}
    assert names == {"v.mp4", "cover.jpg"}
    for m in out:
        assert m["url"] == f"/downloads/douyin_abc/{m['filename']}"


def test_no_media_returns_empty(dl):
    out = media_mod.extract_media("just some plain text tool result, no media here")
    assert out == []


def test_empty_content_returns_empty(dl):
    assert media_mod.extract_media("") == []
    assert media_mod.extract_media(None) == []  # type: ignore[arg-type]


def test_path_outside_downloads_skipped(dl):
    """路径不在 downloads_dir 下的跳过（防越界）。"""
    other = dl.parent / "secret"
    other.mkdir()
    (other / "stolen.mp4").write_bytes(b"x")
    content = _social_json(("video", other / "stolen.mp4"))
    out = media_mod.extract_media(content)
    assert out == []


def test_windows_backslash_to_forward_slash(dl):
    """Windows 反斜杠路径 → url 用正斜杠（json.dumps 转义后 Path 仍能解析）。"""
    content = _social_json(("video", dl / "douyin_abc" / "v.mp4"))
    out = media_mod.extract_media(content)
    assert out
    assert "\\" not in out[0]["url"]
    assert out[0]["url"] == "/downloads/douyin_abc/v.mp4"


def test_dedup_paths(dl):
    """同一文件出现多次去重。"""
    content = _social_json(
        ("video", dl / "douyin_abc" / "v.mp4"),
        ("video", dl / "douyin_abc" / "v.mp4"),
    )
    out = media_mod.extract_media(content)
    assert len(out) == 1


def test_non_media_type_skipped(dl):
    """type=comment（非媒体）且扩展名非媒体 → 跳过。"""
    (dl / "douyin_abc" / "c.json").write_text("{}")
    content = _social_json(("comment", dl / "douyin_abc" / "c.json"))
    assert media_mod.extract_media(content) == []
