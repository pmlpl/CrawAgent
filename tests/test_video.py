"""P7 影视内容验收测试：视频提取 / 广告移除 / 登录态 / 下载。"""

import asyncio
import os

from crawagent.extractors.ad_remover import AdRemover
from crawagent.extractors.video_extractor import VideoExtractor
from crawagent.output.media_downloader import MediaDownloader
from crawagent.sessions.profile_manager import ProfileManager


def _await(coro):
    return asyncio.run(coro)


VIDEO_HTML = """
<html><body>
  <video controls poster="/poster.jpg">
    <source src="/media/movie.mp4" type="video/mp4">
    <source src="/media/movie.m3u8" type="application/x-mpegURL">
  </video>
  <video src="/media/clip.webm"></video>
  <iframe src="https://www.youtube.com/embed/abc123"></iframe>
  <div data-video="/media/ad.mp4"></div>
  <script>var url = "https://cdn.example.com/stream/live.m3u8";</script>
</body></html>
"""


def test_video_extractor():
    result = VideoExtractor().extract(VIDEO_HTML, base_url="https://example.com/page")
    assert result.has_videos
    assert result.total_count >= 6
    all_urls = (
        [v.url for v in result.videos]
        + [v.url for v in result.iframes]
        + result.m3u8_urls
        + result.raw_mp4_urls
    )
    joined = "\n".join(all_urls)
    assert "https://example.com/media/movie.mp4" in joined
    assert "https://example.com/media/movie.m3u8" in joined
    assert "https://www.youtube.com/embed/abc123" in joined
    assert "cdn.example.com/stream/live.m3u8" in joined


def test_video_extractor_empty():
    result = VideoExtractor().extract("")
    assert not result.has_videos
    assert result.total_count == 0


def test_ad_remover():
    html = """
    <html><body>
      <div class="ad-banner">广告</div>
      <div id="ad-1">广告2</div>
      <script src="https://pagead2.googlesyndication.com/ads.js"></script>
      <article><p>正文内容</p></article>
    </body></html>
    """
    cleaned = AdRemover().remove(html)
    assert "ad-banner" not in cleaned
    assert "ad-1" not in cleaned
    assert "googlesyndication" not in cleaned
    assert "正文内容" in cleaned


def test_profile_manager(tmp_path):
    pm = ProfileManager(base_dir=str(tmp_path / "profiles"))
    url = "https://www.bilibili.com/video/BV1xx"
    assert not pm.has_profile(url)
    # 模拟登录完成后写入的元数据
    domain = pm._domain_key(url)
    pm._metadata[domain] = {
        "url": url,
        "cookies_count": 3,
        "created_at": 0.0,
        "updated_at": 1.0,
        "profile_dir": str(tmp_path / "profiles" / domain),
    }
    pm._save_metadata()
    assert pm.has_profile(url)
    info = pm.get_profile_info(url)
    assert info is not None
    assert info.get("cookies_count") == 3
    assert len(pm.list_profiles()) == 1
    # 未登录的 URL 用 profile 抓取应明确报错
    import pytest as _pytest
    with _pytest.raises(ValueError):
        _await(pm.fetch_with_profile("https://example.com/"))
    assert pm.delete_profile(url)
    assert not pm.has_profile(url)


def test_media_downloader_error_and_detect():
    dl = MediaDownloader(base_dir=".")
    r = _await(dl.download("", title="空"))
    assert r["success"] is False and "missing url" in r["error"]
    assert dl._is_video_site("https://www.youtube.com/watch?v=abc")
    assert dl._is_video_site("https://www.bilibili.com/video/BV1")
    assert not dl._is_video_site("https://example.com/a.pdf")


OG_VIDEO_HTML = """
<html><head><title>视频页</title>
<meta property="og:video" content="https://player.bilibili.com/player.html?bvid=BV1xx411c7mD">
<meta property="og:image" content="https://i0.hdslb.com/bfs/cover.jpg">
<meta property="og:title" content="我的视频标题">
</head><body>
  <video src="blob:https://www.bilibili.com/e415685c-8ed3"></video>
  <iframe src="https://lf-rc1.yhgfb-cn-static.com/obj/rc-verifycenter/rmc-nocaptcha/1.0.0.44/index.html"></iframe>
  <iframe src="https://www.youtube.com/embed/abc123"></iframe>
  <img src="https://cdn.example.com/cover/photo_1.jpg" alt="摄影作品">
</body></html>
"""


def test_video_extractor_og_meta():
    """og:video 声明应提取为可点击播放链接（B站/YouTube 等视频站标准）。"""
    result = VideoExtractor().extract(OG_VIDEO_HTML, base_url="https://www.bilibili.com/video/BV1xx411c7mD")
    urls = [v.url for v in result.videos]
    assert any("player.bilibili.com" in u for u in urls)
    # og:video 带标题与封面
    og = [v for v in result.videos if v.source_tag == "og:video"]
    assert og and og[0].title == "我的视频标题"
    assert og[0].poster == "https://i0.hdslb.com/bfs/cover.jpg"


def test_video_extractor_filters_noise():
    """blob: 伪 URL、验证码/存储 iframe 应被过滤，视频 iframe 保留。"""
    result = VideoExtractor().extract(OG_VIDEO_HTML, base_url="https://www.bilibili.com/video/BV1xx411c7mD")
    all_urls = [v.url for v in result.videos] + [v.url for v in result.iframes]
    joined = "\n".join(all_urls)
    # blob: 伪 URL 被过滤
    assert "blob:" not in joined
    # 验证码/存储 iframe 被过滤
    assert "rmc-nocaptcha" not in joined
    assert "x-storage-web" not in joined
    # 已知视频平台 iframe 保留
    assert "youtube.com/embed/abc123" in joined


def test_security_hook_in_loop_blocks_save(tmp_path):
    """集成：SecurityHook 拦截的 save 在 loop 层被拒绝执行。"""
    from crawagent.harness.hooks import CrawlHooks
    from crawagent.harness.types import HookEvent
    from crawagent.harness.loop import CrawlLoop
    from crawagent.security.security_hook import create_security_hooks

    async def fake_save(args):
        raise AssertionError("危险 save 不应被执行")

    class FakeSession:
        async def start_operation(self, *a, **k): return "op"
        async def finish_operation(self, *a, **k): pass
        async def append_entry(self, *a, **k): pass
        async def add_operation_entry(self, *a, **k): pass
        async def get_entries(self, limit=100): return []
        async def get_leaf_entry(self, *a, **k): return None

    hooks = CrawlHooks()
    create_security_hooks(hooks)
    loop = CrawlLoop(
        session=FakeSession(),
        hooks=hooks,
        tools=[],
        tool_executors={"save": fake_save},
    )
    tool_call = {
        "id": "tc1",
        "function": {"name": "save", "arguments": {"path": "/etc/passwd", "content": "x"}},
    }
    ctx = _await(hooks.fire(HookEvent.BEFORE_TOOL, {"tool_calls": [tool_call], "lane": "main"}))
    result = _await(loop._execute_single_tool(ctx["tool_calls"][0]))
    assert result.get("blocked") is True
    assert result.get("error") is True


def test_video_api_endpoints_exist():
    from crawagent.api.server import app
    paths = {getattr(r, "path", "") for r in app.routes}
    for expected in ("/api/video/extract", "/api/video/info", "/api/video/download",
                     "/api/video/ad-remove", "/api/video/stream", "/api/video/files"):
        assert expected in paths, f"缺少路由 {expected}"
