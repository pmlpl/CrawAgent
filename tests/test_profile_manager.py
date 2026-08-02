"""ProfileManager 测试：storage_state.json → Netscape cookies.txt 导出"""
import json

import pytest

from crawagent.sessions.profile_manager import ProfileManager


def _make_storage_state(tmp_path, domain="www.douyin.com", cookies=None):
    """构造一个带 storage_state.json 的 profile 目录"""
    profile_dir = tmp_path / domain
    profile_dir.mkdir(parents=True, exist_ok=True)
    state = {
        "cookies": cookies or [
            {
                "name": "sessionid",
                "value": "abc123",
                "domain": ".douyin.com",
                "path": "/",
                "expires": 1750000000,
                "httpOnly": True,
                "secure": True,
                "sameSite": "Lax",
            },
            {
                "name": "ttwid",
                "value": "guest_token",
                "domain": "www.douyin.com",
                "path": "/",
                "expires": -1,
                "httpOnly": False,
                "secure": False,
            },
        ],
        "origins": [],
    }
    (profile_dir / "storage_state.json").write_text(
        json.dumps(state), encoding="utf-8"
    )
    return profile_dir


def test_export_cookies_txt_converts_format(tmp_path):
    """转换结果必须是 Netscape 格式且字段正确"""
    pm = ProfileManager(base_dir=str(tmp_path))
    _make_storage_state(tmp_path)

    cookies_file = pm.export_cookies_txt("https://www.douyin.com/")

    lines = open(cookies_file, encoding="utf-8").read().splitlines()
    assert lines[0].startswith("# Netscape")
    assert len(lines) == 4  # 2 注释 + 2 cookie

    # 第一条：.douyin.com（带点 → includeSubdomains=TRUE, secure=TRUE）
    fields = lines[2].split("\t")
    assert fields[0] == ".douyin.com"
    assert fields[1] == "TRUE"
    assert fields[2] == "/"
    assert fields[3] == "TRUE"
    assert fields[4] == "1750000000"
    assert fields[5] == "sessionid"
    assert fields[6] == "abc123"

    # 第二条：www.douyin.com（不带点 → FALSE, 会话 cookie expires=0）
    fields = lines[3].split("\t")
    assert fields[0] == "www.douyin.com"
    assert fields[1] == "FALSE"
    assert fields[3] == "FALSE"
    assert fields[4] == "0"
    assert fields[5] == "ttwid"
    assert fields[6] == "guest_token"


def test_export_cookies_txt_escapes_value(tmp_path):
    """value 中的制表符/换行必须被转义，防止破坏格式"""
    pm = ProfileManager(base_dir=str(tmp_path))
    _make_storage_state(
        tmp_path,
        domain="example.com",
        cookies=[{
            "name": "odd",
            "value": "a\tb\nc",
            "domain": "example.com",
            "path": "/",
            "expires": 0,
            "httpOnly": False,
            "secure": False,
        }],
    )

    cookies_file = pm.export_cookies_txt("https://example.com/")
    content = open(cookies_file, encoding="utf-8").read()
    # 整个文件每行恰好 7 个字段（不含空行）
    for line in content.splitlines():
        if line.startswith("#") or not line.strip():
            continue
        assert len(line.split("\t")) == 7, f"字段数异常: {line}"
    assert "a%09b%0Ac" in content


def test_export_cookies_txt_missing_storage(tmp_path):
    """无 storage_state.json 时必须报错提示先登录"""
    pm = ProfileManager(base_dir=str(tmp_path))
    with pytest.raises(FileNotFoundError, match="login_interactive"):
        pm.export_cookies_txt("https://www.douyin.com/")


def test_has_profile_requires_cookies(tmp_path):
    """has_profile 仅在存在有效 cookie 时才为 True"""
    pm = ProfileManager(base_dir=str(tmp_path))
    url = "https://www.douyin.com/"

    assert not pm.has_profile(url)

    # 空 cookie 列表不算已登录
    _make_storage_state(tmp_path, cookies=[])
    # 直接注入元数据模拟 login_interactive 完成
    pm._metadata[pm._domain_key(url)] = {
        "cookies_count": 0,
        "url": url,
    }
    assert not pm.has_profile(url)

    # 有 cookie 才算已登录
    _make_storage_state(tmp_path, cookies=[{
        "name": "sessionid", "value": "x", "domain": ".douyin.com",
        "path": "/", "expires": 1750000000,
        "httpOnly": True, "secure": True,
    }])
    pm._metadata[pm._domain_key(url)] = {
        "cookies_count": 1,
        "url": url,
    }
    assert pm.has_profile(url)


def test_write_netscape_dedup(tmp_path):
    """_write_netscape 对同名同域同路径 cookie 去重"""
    cookies = [
        {"name": "ttwid", "value": "v1", "domain": ".douyin.com", "path": "/",
         "expires": 0, "httpOnly": False, "secure": False},
        {"name": "ttwid", "value": "v2", "domain": ".douyin.com", "path": "/",
         "expires": 0, "httpOnly": False, "secure": False},  # 重复 → 应跳过
        {"name": "msToken", "value": "tok", "domain": "www.douyin.com", "path": "/",
         "expires": 0, "httpOnly": False, "secure": False},
    ]
    out = tmp_path / "cookies.txt"
    n = ProfileManager._write_netscape(cookies, out)
    content = out.read_text(encoding="utf-8")
    assert n == 2
    assert content.count("ttwid") == 1
    assert "msToken" in content


def test_media_downloader_needs_anonymous_cookie(tmp_path):
    """MediaDownloader 仅对 douyin 等风控站自动取匿名 cookie"""
    from crawagent.output.media_downloader import MediaDownloader
    dl = MediaDownloader(base_dir=str(tmp_path))
    assert dl._needs_anonymous_cookie("https://www.douyin.com/video/123")
    assert dl._needs_anonymous_cookie("https://v.douyin.com/abcd/")
    assert not dl._needs_anonymous_cookie("https://www.bilibili.com/video/BV1")
    assert not dl._needs_anonymous_cookie("https://www.youtube.com/watch?v=x")
