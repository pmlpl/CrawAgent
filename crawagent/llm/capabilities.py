"""模型能力预标注与合并（033）— 视觉线命名规律 pattern 表。

三层合并（get_caps，优先级从高到低）：
  1. 显式探测结果（probe，持久化在 provider 条目的 caps 键）——永远覆盖一切推测；
  2. 手动勾选（vision_manual，用户声明"我知道这个中转映射就是视觉模型"）；
  3. pattern 命名规律推测（match_vision_pattern）。

纯函数模块：只吃 provider dict，不 import web / tools 层。
探测结果的数据结构（provider["caps"][model]）：
  {"vision": true|false, "structured": true|false,
   "vision_reply": "模型原话", "structured_reply": "模型原话", "vision_manual": bool}
"""
from __future__ import annotations

import re

# 视觉线命名规律（小写正则，re.search）。命中 → 初始标"视觉（推测）"。
# 校准原则：只标有把握的视觉线命名；漏标只会显示"未验证待测"（探测一下即可），
# 误标会被显式探测结果纠正——所以宁可少标，不猜。
_VISION_RULES: list[tuple[str, str]] = [
    # 智谱 GLM 视觉线：glm-4v / glm-4v-flash / glm-4.5v / glm-5v（末尾或后缀 -v）
    (r"glm-[\d.]+v($|-)", "GLM 视觉线（命名规律）"),
    # 通义 Qwen-VL 线：qwen-vl / qwen2-vl / qwen2.5-vl / qwen3-vl（含任意后缀）
    (r"qwen[\w.]*-vl", "Qwen-VL 视觉线（命名规律）"),
    # OpenAI 多模态：gpt-4o 全系 / gpt-4.1 全系 / gpt-4-turbo（vision 可用）
    (r"gpt-4o", "GPT-4o 多模态（命名规律）"),
    (r"gpt-4\.1", "GPT-4.1 多模态（命名规律）"),
    (r"gpt-4-turbo", "GPT-4 Turbo Vision（命名规律）"),
    # Anthropic：Claude 3 起全线多模态（3/4 系及 sonnet/opus/haiku 后缀命名）
    (r"claude-(3|4|sonnet|opus|haiku)", "Claude 3+ 多模态（命名规律）"),
    # Google：Gemini 1.5 起多模态（gemini-1.5-*/gemini-2.*/gemini-pro-*）
    (r"gemini-(1\.5|2|3)", "Gemini 1.5+ 多模态（命名规律）"),
    (r"gemini-pro", "Gemini Pro 多模态（命名规律）"),
    # 开源视觉线
    (r"llama-?vision", "Llama Vision（命名规律）"),
    (r"pixtral", "Mistral Pixtral 视觉线（命名规律）"),
    (r"internvl", "InternVL（命名规律）"),
    (r"doubao-[\w.]*vision", "豆包视觉线（命名规律）"),
    (r"hunyuan-[\w.]*vision", "混元视觉线（命名规律）"),
    (r"moonshot-[\w.]*vision", "Moonshot 视觉线（命名规律）"),
    # 兜底：名字里带 vision 的基本都是视觉线（llama3.2-vision / doubao-1.5-vision-pro 等）
    (r"vision", "名字含 vision（命名规律）"),
]


def match_vision_pattern(model: str) -> str:
    """按命名规律推测视觉能力。

    Returns:
        命中返回规则说明（如 "GLM 视觉线（命名规律）"）→ 视觉（推测）；
        未命中返回 ""（未验证，不猜文本/视觉）。
    """
    name = (model or "").lower().strip()
    if not name:
        return ""
    for pat, note in _VISION_RULES:
        if re.search(pat, name):
            return note
    return ""


def get_caps(provider: dict, model: str) -> dict:
    """合并三层能力信号 → 前端徽章与四档挑选共用的统一视图。

    Returns:
        {
          "vision": True|False|None,     # 有效视觉判定（None = 未验证）
          "vision_source": "probe"|"manual"|"pattern"|None,
          "vision_manual": bool,         # 手动勾选框原始状态（勾选即用户声明）
          "structured": True|False|None, # 结构化输出探测结果（无 pattern/手动，只认探测）
          "vision_reply": str, "structured_reply": str,  # 模型原话（探测时留证）
        }
    """
    raw = (provider.get("caps") or {}).get(model) or {}
    vision_probe = raw.get("vision")  # True / False / None
    note = match_vision_pattern(model)
    vision_manual = bool(raw.get("vision_manual"))

    if vision_probe is not None:
        vision_effective, source = bool(vision_probe), "probe"
    elif vision_manual:
        vision_effective, source = True, "manual"
    elif note:
        vision_effective, source = True, "pattern"
    else:
        vision_effective, source = None, None

    structured = raw.get("structured")
    return {
        "vision": vision_effective,
        "vision_source": source,
        "vision_manual": vision_manual,
        "structured": bool(structured) if structured is not None else None,
        "vision_reply": str(raw.get("vision_reply") or ""),
        "structured_reply": str(raw.get("structured_reply") or ""),
    }
