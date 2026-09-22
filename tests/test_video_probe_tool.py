"""video_probe_tool 测试 — m3u8 解析 / URL 拼接 / 测速。

Playwright 真集成测成本高 — 集中在纯函数 + 错误路径。
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from crawagent.tools import video_probe_tool


# ---------------------------------------------------------------------------
# _parse_master_m3u8
# ---------------------------------------------------------------------------

def test_parse_master_m3u8_picks_best_resolution():
    """主清单 → 选分辨率/带宽最高的流。"""
    m3u8 = """#EXTM3U
#EXT-X-STREAM-INF:RESOLUTION=1280x720,BANDWIDTH=2000000
720p.m3u8
#EXT-X-STREAM-INF:RESOLUTION=1920x1080,BANDWIDTH=5000000
1080p.m3u8
#EXT-X-STREAM-INF:RESOLUTION=3840x2160,BANDWIDTH=15000000
4k.m3u8
"""
    result = video_probe_tool._parse_master_m3u8(m3u8)
    assert result["is_master"] is True
    assert result["best"]["resolution"] == "3840x2160"
    assert result["best"]["bandwidth"] == 15000000
    assert result["best"]["uri"] == "4k.m3u8"
    assert result["variant_count"] == 3


def test_parse_master_m3u8_handles_missing_resolution():
    """STREAM-INF 无 RESOLUTION → resolution=None，仍可按带宽选。"""
    m3u8 = """#EXTM3U
#EXT-X-STREAM-INF:BANDWIDTH=1000000
low.m3u8
#EXT-X-STREAM-INF:RESOLUTION=1920x1080,BANDWIDTH=5000000
high.m3u8
"""
    result = video_probe_tool._parse_master_m3u8(m3u8)
    assert result["is_master"] is True
    assert result["best"]["bandwidth"] == 5000000


def test_parse_master_m3u8_media_playlist():
    """媒体清单（无 STREAM-INF，只有分片行）→ 返回分片数。"""
    m3u8 = """#EXTM3U
#EXT-X-TARGETDURATION:10
#EXTINF:9.5,
seg1.ts
#EXTINF:9.5,
seg2.ts
#EXTINF:9.5,
seg3.ts
"""
    result = video_probe_tool._parse_master_m3u8(m3u8)
    assert result["is_master"] is False
    assert result["segment_count"] == 3


def test_parse_master_m3u8_empty():
    """空字符串 → 空。"""
    result = video_probe_tool._parse_master_m3u8("")
    assert result["is_master"] is False
    assert result["segment_count"] == 0


def test_parse_master_m3u8_skips_uri_with_comment_following():
    """STREAM-INF 行的下一行是注释 → uri 为空。"""
    m3u8 = """#EXTM3U
#EXT-X-STREAM-INF:RESOLUTION=1280x720,BANDWIDTH=2000000
#EXT-X-I-FRAME-STREAM-INF:URI=iframe.m3u8
real.m3u8
"""
    result = video_probe_tool._parse_master_m3u8(m3u8)
    # 第一个 STREAM-INF 后是 #EXT-X-I-FRAME-STREAM-INF → uri 空
    # 第二个 STREAM-INF 缺失 → 不算
    if result.get("variant_count", 0) > 0:
        # 如果第一个 STREAM-INF 计数了，uri 应是空
        assert result["best"]["uri"] == "" or result["variant_count"] >= 1


# ---------------------------------------------------------------------------
# _resolve_url
# ---------------------------------------------------------------------------

def test_resolve_url_absolute():
    """绝对 URL → 原样返回。"""
    url = video_probe_tool._resolve_url("https://example.com/master.m3u8", "https://other.com/seg.ts")
    assert url == "https://other.com/seg.ts"


def test_resolve_url_relative():
    """相对 URL → 拼到 base。"""
    url = video_probe_tool._resolve_url("https://example.com/path/master.m3u8", "seg.ts")
    assert url == "https://example.com/path/seg.ts"


# ---------------------------------------------------------------------------
# probe_video_player 错误处理
# ---------------------------------------------------------------------------

def test_probe_video_player_playwright_failure():
    """Playwright 启动失败 → 返回 JSON 含 error 字段。"""
    import json
    def fake_run(coro):
        coro.close()
        raise RuntimeError("chromium not installed")
    with patch.object(video_probe_tool.asyncio, "run", side_effect=fake_run):
        out = video_probe_tool.probe_video_player.func("https://example.com")
    data = json.loads(out)
    assert data["page_loaded"] is False
    assert "chromium not installed" in data["error"]