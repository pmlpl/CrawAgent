"""Supervisor 置信度评估 — 对提取结果打分，<60 分触发浏览器模式自升级。

硬约束：Supervisor must automatically upgrade to browser mode if extract phase fails with confidence <60

评分策略（规则启发式，毫秒级、零 LLM 成本）：
基准分 100，有问题扣分，质量好加分，总分 clamp 到 [0, 100]。

指标：
1. 内容长度：<200 字 -30，>500 字 +10
2. 乱码检测：PUA 码点（\ue000-\uf8ff）占比 >5% -40，>10% -60
3. 反爬标记：含"验证/403/forbidden/access denied/登录"等 -50
4. JS 占位：内容含 <script>/loading/请启用 JavaScript -40
5. 中文占比：<10% 且非纯英文 -20，>30% +10
6. 有效句子：句号/问号/感叹号句子数 <3 -20，>10 +10
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# 反爬/错误页常见关键词（中英文混合，大小写不敏感匹配）
_ANTI_CRAWL_PATTERNS = re.compile(
    r"403\b|404\b|forbidden|access\s*denied|permission\s*denied|access\s*restricted"
    r"|blocked|captcha|verification|verify\s*(your|account|human)"
    r"|please\s*log\s*in|not\s*authorized|unauthorized"
    r"|验证|验证码|人机验证|风险验证|登录|请登录|注册会员|VIP专享|付费解锁"
    r"|内容不存在|页面不存在|出错了|无法访问|访问受限|访问被拒|非法请求",
    re.IGNORECASE,
)

# JS 占位/加载中常见关键词
_JS_PLACEHOLDER_PATTERNS = re.compile(
    r"loading\.\.\.|please\s*enable\s*javascript|requires\s*javascript"
    r"|<script|window\.__|noscript>.*enable"
    r"|正在加载|加载中|请启用 JavaScript|JavaScript\s+is\s+required",
    re.IGNORECASE | re.DOTALL,
)

# PUA 私有区域码点：E000–F8FF，常见于字体加密网站（番茄小说/字节系）
_PUA_CHARS = re.compile(r"[\ue000-\uf8ff]")

# 句子分隔符：句号、问号、感叹号、引号后标点
_SENTENCE_SPLIT = re.compile(r"[。！？.!?]+")


@dataclass
class ConfidenceResult:
    """置信度评估结果"""
    score: int               # 0-100
    reasons: list[str]       # 扣/加分原因列表，用于返回给用户
    should_upgrade: bool     # 是否应自动升级到浏览器模式（score < 60）

    def format_marker(self) -> str:
        """在提取结果末尾附加的标记字符串。

        低分：[LOW CONFIDENCE: 分数, 原因] — 让 Agent 自动切 browse_and_crawl
        高分：[CONFIDENCE: 分数] — 信息性标记
        """
        if self.should_upgrade:
            reasons_str = "; ".join(self.reasons[:4])  # 最多4个原因避免太长
            return f"\n\n[LOW CONFIDENCE: score={self.score}, reasons={reasons_str}]"
        return f"\n\n[CONFIDENCE: score={self.score}]"


def _pua_ratio(text: str) -> float:
    if not text:
        return 0.0
    pua = len(_PUA_CHARS.findall(text))
    return pua / len(text)


def _cjk_ratio(text: str) -> float:
    if not text:
        return 0.0
    cjk = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
    return cjk / len(text)


def _sentence_count(text: str) -> int:
    if not text:
        return 0
    parts = _SENTENCE_SPLIT.split(text)
    return sum(1 for p in parts if len(p.strip()) >= 5)


def _has_enough_letters(text: str) -> bool:
    """判断文本中是否有足够多的英文字母（用于区分纯中文站）。"""
    alpha = sum(1 for c in text if c.isalpha() and ord(c) < 128)
    return alpha >= 50


def evaluate_confidence(
    content: str,
    title: str = "",
    url: str = "",
) -> ConfidenceResult:
    """评估提取结果的置信度。

    Args:
        content: 提取到的正文文本
        title: 页面标题（可选）
        url: 原始 URL（可选，目前未用但留接口）

    Returns:
        ConfidenceResult(score, reasons, should_upgrade)
    """
    score = 100
    reasons: list[str] = []
    title_content = f"{title}\n{content}" if title else content

    # 1. 内容长度
    content_len = len(content.strip())
    if content_len == 0:
        score -= 50
        reasons.append("content is empty")
    elif content_len < 200:
        score -= 30
        reasons.append(f"content too short ({content_len} chars < 200)")
    elif content_len > 500:
        score += 10
        reasons.append(f"content length OK ({content_len} chars)")

    # 2. PUA 乱码检测
    pua = _pua_ratio(content)
    if pua > 0.10:
        score -= 60
        reasons.append(f"PUA garble severe ({pua*100:.0f}% chars in private use area)")
    elif pua > 0.05:
        score -= 40
        reasons.append(f"PUA garble detected ({pua*100:.0f}% chars in private use area)")
    elif pua > 0:
        reasons.append(f"PUA chars minimal ({pua*100:.1f}%, OK)")

    # 3. 反爬 / 错误页标记
    anti = _ANTI_CRAWL_PATTERNS.findall(title_content)
    if anti:
        score -= 50
        reasons.append(f"anti-crawl/error markers: {', '.join(sorted(set(anti[:5])))}")

    # 4. JS 占位 / 未渲染 SPA
    js_placeholder = _JS_PLACEHOLDER_PATTERNS.findall(title_content)
    if js_placeholder:
        score -= 40
        reasons.append("JS placeholder found (likely SPA not rendered)")

    # 5. 中文占比（假设正文应含相当比例中文或英文）
    cjk = _cjk_ratio(content)
    if content_len > 100:  # 太短的不判，避免误报
        if cjk < 0.10 and not _has_enough_letters(content):
            score -= 20
            reasons.append(f"language ratio unusual (CJK={cjk*100:.0f}%, alpha low)")
        elif cjk > 0.30:
            score += 10
            reasons.append(f"language ratio normal (CJK={cjk*100:.0f}%)")

    # 6. 有效句子数
    sents = _sentence_count(content)
    if sents < 3:
        score -= 20
        reasons.append(f"too few sentences ({sents} < 3)")
    elif sents > 10:
        score += 10
        reasons.append(f"sentence count OK ({sents})")

    # clamp
    score = max(0, min(100, score))

    return ConfidenceResult(
        score=score,
        reasons=reasons,
        should_upgrade=score < 60,
    )
