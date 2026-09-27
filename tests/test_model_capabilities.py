"""模型能力预标注 / 两维探测（033）：pattern 表、caps 合并、probe 判定（mock 客户端）。

探测全程不发网络：monkeypatch crawagent.llm.probe._client 返回 fake OpenAI 客户端。
"""
import base64
import sys
import zlib
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from crawagent.llm.capabilities import get_caps, match_vision_pattern
from crawagent.llm.probe import (
    _parse_json_loose,
    _solid_png_b64,
    probe_structured,
    probe_vision,
)


# ── pattern 预标注表 ──

@pytest.mark.parametrize("model", [
    "glm-4v", "glm-4v-flash", "glm-4.5v", "GLM-4V-Flash",
    "qwen-vl-plus", "qwen2-vl-7b", "qwen2.5-vl-72b", "qwen3-vl-max",
    "gpt-4o", "gpt-4o-mini", "gpt-4.1", "gpt-4-turbo",
    "claude-3-5-sonnet", "claude-sonnet-4", "claude-opus-4-1",
    "gemini-1.5-pro", "gemini-2.0-flash",
    "llama-vision", "llama3.2-vision", "pixtral-12b", "internvl2-8b",
    "doubao-1.5-vision-pro",
])
def test_pattern_hits_vision_lines(model):
    assert match_vision_pattern(model) != "", f"{model} 应命中视觉规律"


@pytest.mark.parametrize("model", [
    "glm-5.2", "glm-5.3-flash", "glm-4-air",       # GLM 文本线（无 v 后缀）
    "deepseek-chat", "deepseek-v4-flash", "qwen-max", "qwen-plus",
    "moonshot-v1-128k", "minimax-text", "",
])
def test_pattern_misses_text_lines(model):
    """文本线/未知命名不瞎标：未命中返回空串（未验证待测），不猜 false。"""
    assert match_vision_pattern(model) == "", f"{model} 不应命中视觉规律"


def test_glm_text_line_not_marked():
    """glm-5.2（032 实测无视觉）不能被 glm 规则误伤。"""
    assert match_vision_pattern("glm-5.2") == ""
    assert match_vision_pattern("glm-4-video") == ""  # -video 不是 -v 后缀


# ── get_caps 三层合并 ──

def test_caps_probe_beats_pattern():
    """探测结果永远覆盖 pattern 推测：pattern 说视觉、探测说不行 → 不行。"""
    p = {"name": "中转", "models": ["glm-4v"], "caps": {"glm-4v": {"vision": False}}}
    caps = get_caps(p, "glm-4v")
    assert caps["vision"] is False
    assert caps["vision_source"] == "probe"


def test_caps_manual_fills_gap_and_pattern_fallback():
    """无探测时：手动勾选 > pattern；都没有 → None（未验证）。"""
    p = {"name": "中转", "models": ["glm-5.2", "deepseek-chat"],
         "caps": {"glm-5.2": {"vision_manual": True}}}
    manual = get_caps(p, "glm-5.2")  # 手动勾选（pattern 不命中）
    assert manual["vision"] is True and manual["vision_source"] == "manual"
    assert manual["vision_manual"] is True
    none_ = get_caps(p, "deepseek-chat")
    assert none_["vision"] is None and none_["vision_source"] is None


def test_caps_structured_probe_only():
    """structured 无 pattern/手动途径，只认探测结果。"""
    p = {"name": "中转", "models": ["m1", "m2", "m3"],
         "caps": {"m2": {"structured": True}, "m3": {"structured": False}}}
    assert get_caps(p, "m1")["structured"] is None
    assert get_caps(p, "m2")["structured"] is True
    assert get_caps(p, "m3")["structured"] is False


def test_caps_replies_carried():
    """模型原话随 caps 带出（前端展示判定依据）。"""
    p = {"name": "中转", "models": ["m"],
         "caps": {"m": {"vision": False, "vision_reply": "黑色", "structured": True, "structured_reply": '{"answer":"ok"}'}}}
    caps = get_caps(p, "m")
    assert caps["vision_reply"] == "黑色"
    assert caps["structured_reply"] == '{"answer":"ok"}'


# ── 纯青色 PNG（stdlib 生成）──

def test_solid_png_valid_structure():
    """生成物是真 PNG：签名 + IHDR 尺寸 100x100 + 可 zlib 解压。"""
    import struct
    raw = base64.b64decode(_solid_png_b64())
    assert raw[:8] == b"\x89PNG\r\n\x1a\n"
    # IHDR
    assert raw[12:16] == b"IHDR"
    w, h = struct.unpack(">II", raw[16:24])
    assert (w, h) == (100, 100)
    # IDAT 可解压且行数据为纯青色
    idat_start = raw.index(b"IDAT") + 4
    idat_len = struct.unpack(">I", raw[idat_start - 4:idat_start])[0]
    pixels = zlib.decompress(raw[idat_start:idat_start + idat_len])
    row = b"\x00" + bytes((0, 180, 180)) * 100
    assert pixels == row * 100


def test_solid_png_not_a_common_color():
    """青色 (0,180,180) 非常规，防推理模型瞎猜碰对。"""
    import struct
    raw = base64.b64decode(_solid_png_b64())
    idat_start = raw.index(b"IDAT") + 4
    idat_len = struct.unpack(">I", raw[idat_start - 4:idat_start])[0]
    pixels = zlib.decompress(raw[idat_start:idat_start + idat_len])
    assert pixels[1:4] == bytes((0, 180, 180))  # 首像素纯青色
    assert pixels[1:4] != bytes((0, 0, 0)) and pixels[1:4] != bytes((255, 255, 255))


# ── probe 两维判定（mock OpenAI 客户端）──

class _FakeMessage:
    def __init__(self, content):
        self.content = content


class _FakeChoice:
    def __init__(self, content):
        self.message = _FakeMessage(content)


class _FakeResp:
    def __init__(self, content):
        self.choices = [_FakeChoice(content)]


class _FakeCompletions:
    def __init__(self, content):
        self._content = content
        self.last_kwargs = None

    def create(self, **kwargs):
        self.last_kwargs = kwargs
        if isinstance(self._content, Exception):
            raise self._content
        return _FakeResp(self._content)


class _FakeClient:
    def __init__(self, content):
        self.chat = type("Chat", (), {})()
        self.chat.completions = _FakeCompletions(content)


@pytest.fixture()
def patch_client(monkeypatch):
    """把 probe._client 换成 fake 工厂，按序返回给定客户端。"""
    clients: list[_FakeClient] = []
    holder = {}

    def _factory(base_url, api_key):
        c = clients.pop(0) if clients else _FakeClient(Exception("no scripted response"))
        holder["last"] = c
        return c

    monkeypatch.setattr("crawagent.llm.probe._client", _factory)
    holder["queue"] = clients
    return holder


def test_probe_vision_cyan_passes(patch_client):
    """回复含 青/cyan → 视觉。"""
    for reply, expect in (("青色", True), ("cyan", True), ("蓝绿色", True), ("这是青色 (Cyan)", True)):
        patch_client["queue"].append(_FakeClient(reply))
        ok, reply_out = probe_vision("http://x/v1", "sk", "m")
        assert ok is expect and reply_out == reply


def test_probe_vision_wrong_or_empty_fails(patch_client):
    """其它颜色 / 空回复 / 报错 → 非视觉（原话留证）。"""
    for reply, note in (("黑色", "黑色"), ("", "（空回复）")):
        patch_client["queue"].append(_FakeClient(reply))
        ok, reply_out = probe_vision("http://x/v1", "sk", "m")
        assert ok is False and note in reply_out
    patch_client["queue"].append(_FakeClient(Exception("400 Bad Request")))
    ok, reply_out = probe_vision("http://x/v1", "sk", "m")
    assert ok is False and "400" in reply_out


def test_probe_vision_sends_cyan_image(patch_client):
    """请求体确实带 100x100 纯青色图 + 只答颜色词的提问 + 思考余量 max_tokens。"""
    patch_client["queue"].append(_FakeClient("青色"))
    probe_vision("http://x/v1", "sk", "m")
    kw = patch_client["last"].chat.completions.last_kwargs
    assert kw["max_tokens"] == 200  # 思考型模型 reasoning 也要占预算（glm-5.2 实测）
    parts = kw["messages"][0]["content"]
    assert parts[0]["text"] == "这张图是什么颜色？只答颜色词"
    data_url = parts[1]["image_url"]["url"]
    assert data_url.startswith("data:image/png;base64,")


def test_probe_structured_json_passes(patch_client):
    """可解析 JSON（含 answer 键）→ 通过；镜像 browser-use 的 json_schema response_format。"""
    for reply in ('{"answer": "ok"}', '```json\n{"answer": "ok"}\n```', '好的：{"answer": "done"}'):
        patch_client["queue"].append(_FakeClient(reply))
        ok, _ = probe_structured("http://x/v1", "sk", "m")
        assert ok is True, reply
    kw = patch_client["last"].chat.completions.last_kwargs
    assert kw["response_format"]["type"] == "json_schema"
    assert kw["response_format"]["json_schema"]["schema"]["properties"]["answer"] == {"type": "string"}


def test_probe_structured_garbage_or_error_fails(patch_client):
    """非 JSON / 缺 answer / 报错（400/不透传）→ 失败。"""
    for reply in ("我不会输出 JSON", '{"foo": 1}', ""):
        patch_client["queue"].append(_FakeClient(reply))
        ok, _ = probe_structured("http://x/v1", "sk", "m")
        assert ok is False, reply
    patch_client["queue"].append(_FakeClient(Exception("400 response_format not supported")))
    ok, reply_out = probe_structured("http://x/v1", "sk", "m")
    assert ok is False and "400" in reply_out


def test_parse_json_loose():
    assert _parse_json_loose('{"answer": "x"}') == {"answer": "x"}
    assert _parse_json_loose('```json\n{"answer": "x"}\n```') == {"answer": "x"}
    assert _parse_json_loose('前置杂讯 {"answer": "x"} 后缀') == {"answer": "x"}
    assert _parse_json_loose("not json") is None
    assert _parse_json_loose("") is None
