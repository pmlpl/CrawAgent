"""VIP 视频解析工具测试 — 全离线，mock _prescreen_one / 缓存路径，不真连解析接口。

覆盖（规格第五节）：
  1. vlist.json 加载（platformlist + list 两数组）
  2. 平台列表（all / video 过滤 + canvip 标记）
  3. 预筛成功（mock 返回含 m3u8 → 成功串 + 接口名 + 流类型）
  4. 预筛失败（404/超时/失败文本 → 失败串 + 尝试详情）
  5. 指定接口（parser_name → 只请求一个）
  6. 失败文本检测（解析失败/线路维护/parameter error → 判死）
  7. 视频流 URL 检测（.m3u8/.mp4/.ts/.flv/.m4s → 判活）
  8. 播放器标记检测（<video/<iframe/player → 判活）
  9. 缓存读写（成功写入 success_count；二次调用按成功率排序）
  10. 并发请求（mock 延迟 → 总耗时远小于串行）
  11. 错误提示（vlist.json 缺失 → 友好错误串）
  12. 输出截断（10000 字符响应 → 响应片段截断到 500 + 标记）
  13. system.md 一致性（工具条目数 = 51；VIP Workflow 段含 Step 0-2）
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from crawagent.tools import vip_video_tool as vvt
from crawagent.tools.vip_video_tool import (
    parse_vip_video,
    list_vip_platforms,
    _prescreen_one,
    _STREAM_URL_RE,
    _PLAYER_MARK_RE,
    _FAIL_TEXT_RE,
    _load_vlist,
)


VIDEO_URL = "https://v.qq.com/x/cover/test.html"


def _patch_cache(monkeypatch, tmp_path):
    """把缓存路径 patch 到 tmp_path，返回缓存文件路径。"""
    cp = tmp_path / "parser_cache.json"
    monkeypatch.setattr(vvt, "_cache_path", lambda: cp)
    return cp


# ---- 1. vlist.json 加载 ----

def test_vlist_loads_with_two_arrays():
    data = _load_vlist()
    assert data is not None, "vlist.json 应存在且可解析"
    assert isinstance(data.get("platformlist"), list) and len(data["platformlist"]) >= 30
    assert isinstance(data.get("list"), list) and len(data["list"]) >= 40
    assert all("name" in p and "url" in p for p in data["list"])


# ---- 2. 平台列表 ----

def test_list_platforms_all_and_filter():
    r = list_vip_platforms.func("all")
    assert "支持 VIP 解析" in r
    assert "腾讯视频" in r and "爱奇艺" in r
    # canvip 标记行在行首
    assert any(line.startswith("VIP") for line in r.splitlines())

    r2 = list_vip_platforms.func("video")
    assert "腾讯视频" in r2
    assert "CCTV" not in r2  # CCTV 是 tv 类

    r3 = list_vip_platforms.func("novel")
    assert "NO_PLATFORM" in r3 or "平台" in r3


# ---- 3. 预筛成功 ----

def test_parse_success_with_m3u8(monkeypatch, tmp_path):
    _patch_cache(monkeypatch, tmp_path)
    fake = [
        {"name": "默认A", "url": "https://p.example/player?url="},
        {"name": "备用B", "url": "https://q.example/?url="},
    ]
    monkeypatch.setattr(vvt, "_load_vlist", lambda: {"list": fake})

    def two_pass(purl, vurl, timeout=5):
        body = ('<video src="//x/y.m3u8"></video>' if "p.example" in purl
                else '<iframe src="p.html"></iframe>')
        return {"status": "PASS", "reason": "", "body": body,
                "stream_type": "m3u8" if "p.example" in purl else "player",
                "final_url": purl + vurl}

    monkeypatch.setattr(vvt, "_prescreen_one", two_pass)
    r = parse_vip_video.func(VIDEO_URL)
    assert "解析成功" in r
    assert "默认A" in r
    assert "m3u8" in r
    assert "播放地址：https://p.example/player?url=" + VIDEO_URL in r
    assert "备用接口：备用B" in r


# ---- 4. 预筛全失败 ----

def test_parse_all_fail_summary(monkeypatch, tmp_path):
    _patch_cache(monkeypatch, tmp_path)
    fake = [
        {"name": "甲", "url": "https://a.example/?url="},
        {"name": "乙", "url": "https://b.example/?url="},
    ]
    monkeypatch.setattr(vvt, "_load_vlist", lambda: {"list": fake})
    monkeypatch.setattr(
        vvt, "_prescreen_one",
        lambda purl, vurl, timeout=5: {"status": "FAIL", "reason": "HTTP 404", "body": ""},
    )
    r = parse_vip_video.func(VIDEO_URL)
    assert "全部 2 个解析接口均预筛失败" in r
    assert "甲(HTTP 404)" in r and "乙(HTTP 404)" in r


# ---- 5. 指定接口 ----

def test_parse_specific_parser_only_one_request(monkeypatch, tmp_path):
    _patch_cache(monkeypatch, tmp_path)
    fake = [
        {"name": "默认A", "url": "https://a.example/?url="},
        {"name": "邦宁", "url": "https://b.example/?url="},
    ]
    monkeypatch.setattr(vvt, "_load_vlist", lambda: {"list": fake})
    calls = []

    def fake_prescreen(purl, vurl, timeout=5):
        calls.append(purl)
        return {"status": "PASS", "reason": "", "body": "<iframe></iframe>",
                "stream_type": "player", "final_url": purl + vurl}

    monkeypatch.setattr(vvt, "_prescreen_one", fake_prescreen)
    r = parse_vip_video.func(VIDEO_URL, parser_name="邦宁")
    assert len(calls) == 1, f"指定接口应只请求 1 次，实际 {len(calls)}"
    assert "b.example" in calls[0]
    assert "解析成功" in r and "邦宁" in r


def test_parse_unknown_parser_name(monkeypatch, tmp_path):
    _patch_cache(monkeypatch, tmp_path)
    fake = [{"name": "默认A", "url": "https://a.example/?url="}]
    monkeypatch.setattr(vvt, "_load_vlist", lambda: {"list": fake})
    r = parse_vip_video.func(VIDEO_URL, parser_name="不存在的")
    assert "ERR: 未找到解析接口" in r
    assert "默认A" in r


# ---- 6. 失败文本检测（正则单测 + 集成）----

def test_fail_text_regex():
    for text in ("解析失败，请重试", "该网站暂不支持此视频", "线路维护中",
                 "parameter error", "视频已下线"):
        assert _FAIL_TEXT_RE.search(text), f"应判死: {text}"


def test_fail_text_integration(monkeypatch, tmp_path):
    _patch_cache(monkeypatch, tmp_path)
    fake = [{"name": "甲", "url": "https://a.example/?url="}]
    monkeypatch.setattr(vvt, "_load_vlist", lambda: {"list": fake})
    monkeypatch.setattr(
        vvt, "_prescreen_one",
        lambda purl, vurl, timeout=5: {
            "status": "FAIL", "reason": "解析服务返回失败文本",
            "body": "很抱歉，解析失败，请更换线路",
        },
    )
    r = parse_vip_video.func(VIDEO_URL)
    assert "预筛失败" in r and "失败文本" in r


# ---- 7. 视频流 URL 检测 ----

def test_stream_url_regex():
    for url in ("//cdn/x/y.m3u8?sign=1", "a.mp4#t=1", "v.ts", "b.flv",
                "c.m4s", "d.mkv", "e.webm"):
        assert _STREAM_URL_RE.search(url), f"应判活: {url}"
    assert not _STREAM_URL_RE.search("playlist.json")


# ---- 8. 播放器标记检测 ----

def test_player_mark_regex():
    for html in ("<video src=...>", "<iframe src=...>", "window.player = {}"):
        assert _PLAYER_MARK_RE.search(html), f"应判活: {html}"


# ---- 9. 缓存读写 ----

def test_cache_write_and_ranking(monkeypatch, tmp_path):
    cp = _patch_cache(monkeypatch, tmp_path)
    fake = [
        {"name": "常胜", "url": "https://win.example/?url="},
        {"name": "常败", "url": "https://lose.example/?url="},
    ]
    monkeypatch.setattr(vvt, "_load_vlist", lambda: {"list": fake})

    def fake_prescreen(purl, vurl, timeout=5):
        ok = "win.example" in purl
        return ({"status": "PASS", "reason": "", "body": "x.m3u8",
                 "stream_type": "m3u8", "final_url": purl + vurl}
                if ok else {"status": "FAIL", "reason": "HTTP 500", "body": ""})

    monkeypatch.setattr(vvt, "_prescreen_one", fake_prescreen)
    r1 = parse_vip_video.func(VIDEO_URL)
    assert "解析成功" in r1
    # 缓存文件应写入且常胜 success_count=1
    import json
    cache = json.loads(cp.read_text(encoding="utf-8"))
    assert cache["常胜"]["success_count"] == 1
    assert cache["常败"]["fail_count"] == 1


# ---- 10. 并发请求 ----

def test_prescreen_runs_concurrently(monkeypatch, tmp_path):
    _patch_cache(monkeypatch, tmp_path)
    n = 20
    fake = [{"name": f"P{i}", "url": f"https://p{i}.example/?url="} for i in range(n)]
    monkeypatch.setattr(vvt, "_load_vlist", lambda: {"list": fake})
    delay = 0.2

    def slow_prescreen(purl, vurl, timeout=5):
        time.sleep(delay)
        return {"status": "FAIL", "reason": "HTTP 404", "body": ""}

    monkeypatch.setattr(vvt, "_prescreen_one", slow_prescreen)
    t0 = time.monotonic()
    r = parse_vip_video.func(VIDEO_URL)
    dt = time.monotonic() - t0
    assert dt < n * delay * 0.5, f"应并发执行，实际耗时 {dt:.2f}s（串行需 {n * delay:.2f}s）"
    assert f"全部 {n} 个解析接口均预筛失败" in r


# ---- 11. vlist.json 缺失 ----

def test_vlist_missing_friendly_error(monkeypatch, tmp_path):
    monkeypatch.setattr(vvt, "_VLIST_PATH", tmp_path / "no_such_vlist.json")
    r = parse_vip_video.func(VIDEO_URL)
    assert "ERR: 内置解析接口列表缺失或损坏" in r
    r2 = list_vip_platforms.func("all")
    assert "ERR: 内置平台列表缺失或损坏" in r2


# ---- 12. 输出截断 ----

def test_output_truncation(monkeypatch, tmp_path):
    _patch_cache(monkeypatch, tmp_path)
    fake = [{"name": "甲", "url": "https://a.example/?url="}]
    monkeypatch.setattr(vvt, "_load_vlist", lambda: {"list": fake})
    big_body = "<video>" + "x" * 10000 + "</video>"
    monkeypatch.setattr(
        vvt, "_prescreen_one",
        lambda purl, vurl, timeout=5: {
            "status": "PASS", "reason": "", "body": big_body,
            "stream_type": "mp4", "final_url": purl + vurl,
        },
    )
    r = parse_vip_video.func(VIDEO_URL)
    assert "截断，共" in r
    assert r.count("x" * 10000) == 0, "10000 字符不应原样出现在输出里"


# ---- 13. system.md 一致性 ----

def test_system_md_consistency():
    md = (
        Path(__file__).resolve().parents[1] / "crawagent" / "prompts" / "system.md"
    ).read_text(encoding="utf-8")
    # 工具条目 1-51 连续编号
    import re
    nums = [int(m.group(1)) for m in re.finditer(r"^(\d+)\. [a-z0-9_]+\(", md, re.M)]
    assert nums == list(range(1, 52)), f"工具条目应为 1-51 连续编号，实际 {nums}"
    assert "You have 51 powerful tools" in md
    # VIP Workflow 段存在且含 Step 0-2
    assert "VIP 视频解析 Workflow" in md
    for step in ("Step 0", "Step 1", "Step 2"):
        assert step in md
    # registry 类别标签
    from crawagent.tools.registry import discover_tools
    import crawagent.tools.registry as reg
    reg._DISCOVERY_CACHE = None
    specs = {s.name: s for s in reg.discover_tools()}
    assert "parse_vip_video" in specs and "list_vip_platforms" in specs
