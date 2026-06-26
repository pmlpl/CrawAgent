"""tests/test_url_detection.py - URL 提取和检测函数测试"""
import pytest

from crawagent.tools.base_crawler import extract_urls, looks_like_url
from crawagent.tools.crawler import Crawler


class TestExtractUrls:
    """extract_urls 测试"""

    def test_extract_single_url(self):
        text = "请访问 https://example.com 了解详情"
        urls = extract_urls(text)
        assert "https://example.com" in urls

    def test_extract_multiple_urls(self):
        text = "链接1: https://foo.com 和链接2: http://bar.com"
        urls = extract_urls(text)
        assert len(urls) == 2
        assert "https://foo.com" in urls
        assert "http://bar.com" in urls

    def test_extract_with_www_prefix(self):
        text = "官网: www.example.org"
        urls = extract_urls(text)
        assert any("example.org" in u for u in urls)

    def test_extract_deduplicates(self):
        text = "https://example.com 出现两次 https://example.com"
        urls = extract_urls(text)
        assert urls.count("https://example.com") == 1

    def test_extract_empty_text(self):
        urls = extract_urls("")
        assert urls == []


class TestLooksLikeUrl:
    """looks_like_url 测试"""

    def test_https_url(self):
        assert looks_like_url("https://example.com") is True

    def test_http_url(self):
        assert looks_like_url("http://example.com") is True

    def test_www_url(self):
        assert looks_like_url("www.example.com") is True

    def test_normal_text(self):
        assert looks_like_url("这是一段普通文字") is False

    def test_empty_string(self):
        assert looks_like_url("") is False

    def test_url_with_path(self):
        assert looks_like_url("https://example.com/path/to/page") is True

    def test_strips_whitespace(self):
        assert looks_like_url("  https://example.com  ") is True


class TestCrawlerIsVideoSite:
    """Crawler.is_video_site 静态方法测试"""

    def test_douyin(self):
        assert Crawler.is_video_site("https://www.douyin.com/video/123") is True

    def test_douyin_short(self):
        assert Crawler.is_video_site("https://v.douyin.com/abc") is True

    def test_bilibili(self):
        assert Crawler.is_video_site("https://www.bilibili.com/video/bv123") is True

    def test_youtube(self):
        assert Crawler.is_video_site("https://www.youtube.com/watch?v=abc") is True

    def test_non_video_site(self):
        assert Crawler.is_video_site("https://www.example.com") is False

    def test_invalid_url(self):
        assert Crawler.is_video_site("not a url") is False
