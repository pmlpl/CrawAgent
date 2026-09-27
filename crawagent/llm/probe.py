"""模型能力两维探测（033）— 视觉（纯青色图问颜色）+ JSON 结构化输出。

判定标准（032 实测教训）：**答对颜色才算视觉**——文本模型收图 API 不报错但内容
不进上下文（纯绿图答空、纯红图答"黑"），"没报错"不是信号。纯青色 (0,180,180)
是非常规色，防推理模型瞎猜碰对。

结构化输出镜像 browser-use 0.13 的真实机制：response_format = json_schema
（browser_use/llm/openai/chat.py 的 ResponseFormatJSONSchema），不是 function
calling 也不是 json_object——中转站对 json_schema 的透传参差，探测必须原样复刻。

直接用 openai SDK（browser-use 同源语义），不走 langchain 包装。
"""
from __future__ import annotations

import base64
import json
import struct
import zlib

VISION_QUESTION = "这张图是什么颜色？只答颜色词"
# 回复含任一词即判视觉（青/cyan/蓝绿/青绿；英文统一小写比较）
VISION_JUDGE_WORDS = ("青", "cyan", "蓝绿", "青绿")

ANSWER_SCHEMA = {
    "name": "probe_answer",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {"answer": {"type": "string"}},
        "required": ["answer"],
        "additionalProperties": False,
    },
}

_TRUNC = 120  # 模型原话存证截断


def _solid_png_b64(rgb: tuple[int, int, int] = (0, 180, 180), size: int = 100) -> str:
    """纯色 PNG → base64（stdlib 手写 PNG：IHDR 8bit RGB + 单 IDAT + IEND，无 Pillow 依赖）。"""
    w = h = size
    r, g, b = rgb
    row = b"\x00" + bytes((r, g, b)) * w  # 每行前置 filter 字节 0
    raw = row * h

    def chunk(typ: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data)) + typ + data
            + struct.pack(">I", zlib.crc32(typ + data) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)  # width height bit8 color2(RGB)
    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )
    return base64.b64encode(png).decode()


def _client(base_url: str, api_key: str):
    from openai import OpenAI

    return OpenAI(api_key=api_key, base_url=base_url, timeout=20.0, max_retries=0)


def probe_vision(base_url: str, api_key: str, model: str) -> tuple[bool, str]:
    """视觉探测：发 100x100 纯青色图问颜色，回复含 青/cyan/蓝绿/青绿 才算过。

    max_tokens 给到 200：思考型模型（glm-5.2 实测）会把小预算全花在
    reasoning_content 上、content 返回空——那不是"没看到图"而是"没预算说话"，
    会让视觉模型假阴性。判定标准本身不变：答对颜色才算视觉。

    Returns:
        (是否视觉, 模型原话)——原话含空回复/报错信息，供前端展示判定依据。
    """
    try:
        client = _client(base_url, api_key)
        data_url = "data:image/png;base64," + _solid_png_b64()
        resp = client.chat.completions.create(
            model=model,
            max_tokens=200,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": VISION_QUESTION},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            }],
        )
        reply = (resp.choices[0].message.content or "").strip()
        ok = any(w in reply.lower() for w in VISION_JUDGE_WORDS)
        return ok, (reply[:_TRUNC] or "（空回复）")
    except Exception as e:
        return False, f"调用失败：{type(e).__name__}: {e}"[:_TRUNC]


def probe_structured(base_url: str, api_key: str, model: str) -> tuple[bool, str]:
    """结构化输出探测：response_format=json_schema 强制 {"answer": string}，
    能返回可解析 JSON 即通过；报错（400/不透传）即失败。

    max_tokens 给到 1000：思考型模型 reasoning 也要占预算（glm-5.2 实测
    100 token 全花在思考上 content 返回空，1000 才轮到正文），探测的是
    json_schema 合规性，不是经济性。
    """
    try:
        client = _client(base_url, api_key)
        resp = client.chat.completions.create(
            model=model,
            max_tokens=1000,
            messages=[{
                "role": "user",
                "content": '请只输出一个 JSON 对象，格式为 {"answer": "<任意一句话>"}',
            }],
            response_format={"type": "json_schema", "json_schema": ANSWER_SCHEMA},
        )
        reply = (resp.choices[0].message.content or "").strip()
        parsed = _parse_json_loose(reply)
        if isinstance(parsed, dict) and "answer" in parsed:
            return True, reply[:_TRUNC] or "（空回复）"
        return False, reply[:_TRUNC] or "（空回复）"
    except Exception as e:
        return False, f"调用失败：{type(e).__name__}: {e}"[:_TRUNC]


def _parse_json_loose(text: str):
    """剥掉 ```json 围栏 / 前后杂讯后 json.loads；解析失败返回 None。"""
    if not text:
        return None
    raw = text.strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.lower().startswith("json"):
            raw = raw[4:]
        raw = raw.strip()
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        # 兜底：截取第一个 { 到最后一个 } 再试
        start, end = raw.find("{"), raw.rfind("}")
        if 0 <= start < end:
            try:
                return json.loads(raw[start:end + 1])
            except (json.JSONDecodeError, ValueError):
                return None
        return None
