"""BrowserAPIHarvester 测试：启发式提取逻辑（不依赖真实网络）"""
from crawagent.core.api_harvester import (
    _extract_list_items,
    _extract_media_urls,
    _dedup_items,
    _dedup_media,
    _parse_netscape_cookies,
)


# ==================== _extract_list_items ====================

def test_extract_list_items_picks_container_key():
    """容器键（aweme_list）权重高于普通大数组"""
    data = {
        "data": {
            "aweme_list": [
                {"aweme_id": "1", "desc": "视频A"},
                {"aweme_id": "2", "desc": "视频B"},
            ],
            "config_rules": [{"name": f"rule{i}", "enable": True} for i in range(50)],
        }
    }
    items = _extract_list_items(data)
    assert len(items) == 2
    assert items[0]["desc"] == "视频A"


def test_extract_list_items_picks_media_weight():
    """含 play_addr 的数组权重最高（即使条数少）"""
    data = {
        "config_rules": [{"name": f"r{i}", "enable": True} for i in range(30)],
        "feeds": [
            {"aweme_id": "1", "video": {"play_addr": {"url": "http://cdn/a.mp4"}}},
        ],
    }
    items = _extract_list_items(data)
    assert len(items) == 1
    assert items[0]["aweme_id"] == "1"


def test_extract_list_items_filters_config_records():
    """排除配置类记录（js_error/sample_rate 等）"""
    data = {
        "items": [
            {"name": "js_error", "enable": True, "sample_rate": 0},
            {"name": "js_error2", "enable": True, "sample_rate": 0},
            {"aweme_id": "1", "desc": "真实内容"},
        ]
    }
    items = _extract_list_items(data)
    # 配置类被排除，剩余真实内容
    assert all("desc" in i or "aweme_id" in i for i in items)


def test_extract_list_items_fallback_keeps_content():
    """兜底路径也不放回配置类记录"""
    data = {
        "a": [{"name": "x", "sample_rate": 0}, {"name": "y", "enable": True}],
    }
    items = _extract_list_items(data)
    assert items == []


# ==================== _extract_media_urls ====================

def test_extract_media_urls_by_extension():
    """扩展名识别（mp4）"""
    data = {"video": {"play_addr": {"url_list": ["https://cdn.example.com/v.mp4"]}}}
    medias = _extract_media_urls(data)
    assert len(medias) == 1
    assert medias[0]["url"].endswith(".mp4")


def test_extract_media_urls_by_key():
    """key 命中（play_addr/uri）即使无扩展名也识别"""
    data = {"aweme": {"video": {"play_addr": {"uri": "https://v3-web.douyinvod.com/xyz", "url_list": []}}}}
    medias = _extract_media_urls(data)
    assert len(medias) == 1
    assert medias[0]["key"] == "uri"


def test_extract_media_urls_by_cdn_domain():
    """无扩展名的签名直链（douyinvod 等 CDN 域名）识别"""
    data = {"data": {"main_url": "https://v26-web.douyinvod.com/abc123/video/tos/cn/x.mp4q"}}
    medias = _extract_media_urls(data)
    assert len(medias) == 1
    assert "douyinvod" in medias[0]["url"]


def test_extract_media_urls_dedup_and_skip_non_media():
    """去重 + 跳过普通 URL"""
    data = {
        "links": ["https://www.douyin.com/video/1", "https://www.douyin.com/video/2"],
        "videos": [
            {"play_addr": {"url_list": ["https://cdn.a.com/1.mp4", "https://cdn.a.com/1.mp4"]}},
        ],
    }
    medias = _extract_media_urls(data)
    urls = [m["url"] for m in medias]
    assert len(urls) == 1  # 去重
    assert "douyin.com/video" not in urls  # 普通页面链接不识别


# ==================== _dedup_items ====================

def test_dedup_items_by_id():
    """按 id/url 去重"""
    items = [
        {"aweme_id": "1", "desc": "a"},
        {"aweme_id": "1", "desc": "a-dup"},
        {"aweme_id": "2", "desc": "b"},
    ]
    out = _dedup_items(items)
    assert len(out) == 2


# ==================== _parse_netscape_cookies ====================

def test_parse_netscape_cookies(tmp_path):
    """Netscape cookies.txt → Playwright cookie 列表"""
    f = tmp_path / "cookies.txt"
    f.write_text(
        "# Netscape HTTP Cookie File\n"
        ".douyin.com\tTRUE\t/\tTRUE\t1750000000\tttwid\tvalue123\n"
        "www.example.com\tFALSE\t/\tFALSE\t0\tname\tv1\n",
        encoding="utf-8",
    )
    cookies = _parse_netscape_cookies(str(f))
    assert len(cookies) == 2
    assert cookies[0]["name"] == "ttwid"
    assert cookies[0]["value"] == "value123"
    assert cookies[0]["domain"] == ".douyin.com"
    assert cookies[0]["secure"] is True
    assert cookies[0]["expires"] == 1750000000
    assert cookies[1]["secure"] is False
    assert cookies[1]["expires"] == 0


def test_parse_netscape_cookies_missing(tmp_path):
    """cookies.txt 不存在时返回空列表"""
    assert _parse_netscape_cookies(str(tmp_path / "nope.txt")) == []
