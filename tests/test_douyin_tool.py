"""douyin_tool 测试 — 抖音公开数据抽取。

依赖测试约定：mock `crawagent.tools.social_utils._get` 拦截真实 API。
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from crawagent.tools import douyin_tool


# ---------------------------------------------------------------------------
# _douyin_comments
# ---------------------------------------------------------------------------

def test_douyin_comments_parses_standard_fields():
    """API 返回 comments → 解析 text/user/likes/create_time/reply_count。"""
    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "comments": [
            {"text": "评论1", "user": {"nickname": "用户A"}, "digg_count": 10, "createTime": 1700000000, "reply_comment_total": 2},
            {"text": "评论2", "user": {"nickname": "用户B"}, "digg_count": 5, "createTime": 1700000100, "reply_comment_total": 0},
        ]
    }

    with patch.object(douyin_tool, "_get", return_value=mock_resp):
        comments = douyin_tool._douyin_comments("aweme_12345")
    assert len(comments) == 2
    assert comments[0]["text"] == "评论1"
    assert comments[0]["user"] == "用户A"
    assert comments[0]["likes"] == 10
    assert comments[1]["reply_count"] == 0


def test_douyin_comments_skips_empty_text():
    """空 text 的评论被过滤。"""
    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "comments": [
            {"text": "", "user": {"nickname": "A"}},
            {"text": "   ", "user": {"nickname": "B"}},
            {"text": "有内容", "user": {"nickname": "C"}},
        ]
    }

    with patch.object(douyin_tool, "_get", return_value=mock_resp):
        comments = douyin_tool._douyin_comments("aweme_1")
    assert len(comments) == 1
    assert comments[0]["user"] == "C"


def test_douyin_comments_request_failure_returns_empty():
    """API 失败 → 返回空 list。"""
    import requests as real_requests
    with patch.object(douyin_tool, "_get", side_effect=real_requests.ConnectionError("net")):
        comments = douyin_tool._douyin_comments("aweme_1")
    assert comments == []


def test_douyin_comments_invalid_json_returns_empty():
    """JSON 解析失败 → 返回空 list。"""
    mock_resp = MagicMock()
    mock_resp.json.side_effect = ValueError("bad json")
    with patch.object(douyin_tool, "_get", return_value=mock_resp):
        comments = douyin_tool._douyin_comments("aweme_1")
    assert comments == []


def test_douyin_comments_missing_optional_fields():
    """缺失字段 → 默认值（不抛错）。"""
    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "comments": [
            {"text": "minimal"},
            # no user, no digg_count, no createTime
        ]
    }

    with patch.object(douyin_tool, "_get", return_value=mock_resp):
        comments = douyin_tool._douyin_comments("aweme_1")
    assert len(comments) == 1
    assert comments[0]["text"] == "minimal"
    assert comments[0]["user"] == ""
    assert comments[0]["likes"] == 0
    assert comments[0]["reply_count"] == 0


def test_douyin_comments_passes_correct_params():
    """请求 params 含 aweme_id/cursor/count。"""
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"comments": []}

    with patch.object(douyin_tool, "_get", return_value=mock_resp) as mock_get:
        douyin_tool._douyin_comments("aweme_xyz")
    params = mock_get.call_args.kwargs["params"]
    assert params["aweme_id"] == "aweme_xyz"
    assert params["cursor"] == 0
    assert params["count"] == 20
    # Referer 头
    headers = mock_get.call_args.kwargs["headers"]
    assert "Referer" in headers
    assert "aweme_xyz" in headers["Referer"]


# ---------------------------------------------------------------------------
# douyin_extract / douyin_plan_downloads（公开入口）
# ---------------------------------------------------------------------------

def test_douyin_extract_forwards_to_douyin():
    """douyin_extract 是 1 行转发 → 调 _douyin。"""
    with patch.object(douyin_tool, "_douyin", return_value={"platform": "douyin", "aweme_id": "123"}) as mock_d:
        result = douyin_tool.douyin_extract("https://v.douyin.com/abc/", {"metadata"})
    assert result["platform"] == "douyin"
    mock_d.assert_called_once()


def test_douyin_extract_no_aweme_id_returns_error():
    """URL 无法解析 aweme_id → error 字段。"""
    with patch.object(douyin_tool, "_extract_aweme_id", return_value=""), \
         patch.object(douyin_tool, "_resolve", return_value="https://example.com/no-id"):
        result = douyin_tool._douyin("https://example.com/no-id", {"metadata"})
    assert "error" in result
    assert result["platform"] == "douyin"


# ---------------------------------------------------------------------------
# douyin_plan_downloads
# ---------------------------------------------------------------------------

def test_douyin_plan_downloads_video_url():
    """有 video_url → (video, url, name) 元组。"""
    data = {
        "title": "测试视频",
        "video_url": "https://example.com/video.mp4",
        "video_url_720p": "https://example.com/video_720p.mp4",
    }
    plan = douyin_tool.douyin_plan_downloads(data)
    # 至少 video 一项
    assert any(kind == "video" for kind, _, _ in plan)


def test_douyin_plan_downloads_skip_video_if_no_url():
    """无 video_url → 不生成 video 项。"""
    data = {"title": "无视频", "cover_url": "https://example.com/cover.jpg"}
    plan = douyin_tool.douyin_plan_downloads(data)
    assert not any(kind == "video" for kind, _, _ in plan)


def test_douyin_plan_downloads_images_list():
    """images 列表 → 每张 (image, url, name)。"""
    data = {
        "title": "图集",
        "images": [
            "https://example.com/img1.jpg",
            "https://example.com/img2.jpg",
        ],
    }
    plan = douyin_tool.douyin_plan_downloads(data)
    image_items = [(k, u, n) for k, u, n in plan if k == "image"]
    assert len(image_items) == 2


def test_douyin_plan_downloads_safe_name_used():
    """文件名走 _safe_name 清洗。"""
    data = {"title": "测试/标题:1*", "video_url": "https://e.com/v.mp4"}
    plan = douyin_tool.douyin_plan_downloads(data)
    video_items = [(k, u, n) for k, u, n in plan if k == "video"]
    assert len(video_items) >= 1
    # 文件名应不含非法字符
    for _, _, name in video_items:
        assert "/" not in name
        assert ":" not in name