"""分块策略（P2-4）：7 种分块方式，零外部依赖。

参考 Crawl4AI chunking_strategy.py：
1. IdentityChunking             — 整段返回
2. RegexChunking                — 正则切分
3. SentenceChunking             — 中英文句子切分
4. FixedSizeChunking            — 定长字符块（可选重叠）
5. SlidingWindowChunking        — 滑窗（定步长）
6. OverlappingWindowChunking    — 重叠窗口 + 低重叠合并
7. RecursiveChunking            — 递归字符切分（LangChain 风格）
"""
from __future__ import annotations

import re
from abc import ABC, abstractmethod
from typing import List, Optional


class ChunkingStrategy(ABC):
    """分块策略基类。"""

    @abstractmethod
    def chunk(self, text: str) -> List[str]:
        """把文本切成块列表。"""
        raise NotImplementedError


class IdentityChunking(ChunkingStrategy):
    """整段作为一块。"""

    def chunk(self, text: str) -> List[str]:
        return [text] if text else []


class RegexChunking(ChunkingStrategy):
    """按正则模式逐层切分。"""

    def __init__(self, patterns: Optional[List[str]] = None, **kwargs):
        self.patterns = patterns or [r"\n\n"]

    def chunk(self, text: str) -> List[str]:
        pieces = [text]
        for pattern in self.patterns:
            next_pieces: List[str] = []
            for piece in pieces:
                next_pieces.extend(re.split(pattern, piece))
            pieces = next_pieces
        return [p.strip() for p in pieces if p and p.strip()]


class SentenceChunking(ChunkingStrategy):
    """中英文句子切分（保留句末标点）。"""

    _SENT_SPLIT = re.compile(r"(?<=[。！？!?])\s*|\n+")

    def chunk(self, text: str) -> List[str]:
        parts = self._SENT_SPLIT.split(text)
        return [p.strip() for p in parts if p and p.strip()]


class FixedSizeChunking(ChunkingStrategy):
    """定长字符块，可设置块间重叠。"""

    def __init__(self, chunk_size: int = 500, overlap: int = 0, **kwargs):
        self.chunk_size = max(1, int(chunk_size))
        self.overlap = max(0, min(int(overlap), self.chunk_size - 1))

    def chunk(self, text: str) -> List[str]:
        if not text:
            return []
        chunks: List[str] = []
        start = 0
        n = len(text)
        while start < n:
            end = min(start + self.chunk_size, n)
            chunks.append(text[start:end])
            if end == n:
                break
            start = end - self.overlap if self.overlap else end
        return [c for c in chunks if c]


class SlidingWindowChunking(ChunkingStrategy):
    """滑窗切分：固定窗口大小 + 固定步长。"""

    def __init__(self, window_size: int = 500, step: int = 250, **kwargs):
        self.window_size = max(1, int(window_size))
        self.step = max(1, int(step))

    def chunk(self, text: str) -> List[str]:
        if not text:
            return []
        chunks = []
        n = len(text)
        start = 0
        while start < n:
            chunks.append(text[start : start + self.window_size])
            if start + self.window_size >= n:
                break
            start += self.step
        return chunks


class OverlappingWindowChunking(ChunkingStrategy):
    """重叠窗口 + 低重叠合并（参考 Crawl4AI）。"""

    def __init__(
        self,
        window_size: int = 500,
        overlap: int = 100,
        merge_threshold: int = 0,
        min_chunk_size: int = 0,
        **kwargs,
    ):
        self.window_size = max(1, int(window_size))
        self.overlap = max(0, int(overlap))
        self.merge_threshold = int(merge_threshold)
        self.min_chunk_size = int(min_chunk_size)

    def chunk(self, text: str) -> List[str]:
        if not text:
            return []
        n = len(text)
        step = max(1, self.window_size - self.overlap)
        windows: List[str] = []
        start = 0
        while start < n:
            windows.append(text[start : start + self.window_size])
            if start + self.window_size >= n:
                break
            start += step

        merged: List[str] = []
        for w in windows:
            if not merged:
                merged.append(w)
                continue
            # 与上一块的重叠字符数
            prev = merged[-1]
            overlap_chars = 0
            for i in range(1, min(len(prev), len(w)) + 1):
                if prev[-i:] == w[:i]:
                    overlap_chars = i
            if overlap_chars >= self.merge_threshold:
                merged[-1] = prev + w[overlap_chars:]
            else:
                merged.append(w)

        if self.min_chunk_size:
            merged = [m for m in merged if len(m) >= self.min_chunk_size]
        return merged


class RecursiveChunking(ChunkingStrategy):
    """递归切分：按分隔符层级拆分，再贪心合并到 chunk_size。"""

    DEFAULT_SEPARATORS = ["\n\n", "\n", "。", "！", "？", "!", "?", " ", ""]

    def __init__(
        self,
        separators: Optional[List[str]] = None,
        chunk_size: int = 800,
        **kwargs,
    ):
        self.separators = separators or self.DEFAULT_SEPARATORS
        self.chunk_size = max(1, int(chunk_size))

    def chunk(self, text: str) -> List[str]:
        if not text:
            return []
        pieces = self._split(text, self.separators)
        return self._merge(pieces)

    def _split(self, text: str, seps: List[str]) -> List[str]:
        if len(text) <= self.chunk_size or not seps:
            return [text]
        sep = seps[0]
        if not sep:
            parts = list(text)
        else:
            # 用捕获组保留分隔符，保证切分后可无损重建
            parts = re.split(f"({re.escape(sep)})", text)
            merged_parts: List[str] = []
            i = 0
            while i < len(parts):
                if i + 1 < len(parts) and parts[i + 1] == sep:
                    merged_parts.append(parts[i] + sep)
                    i += 2
                else:
                    merged_parts.append(parts[i])
                    i += 1
            parts = merged_parts
        result: List[str] = []
        for part in parts:
            if not part:
                continue
            if len(part) <= self.chunk_size:
                result.append(part)
            else:
                result.extend(self._split(part, seps[1:]))
        return result

    def _merge(self, pieces: List[str]) -> List[str]:
        chunks: List[str] = []
        buf = ""
        for piece in pieces:
            if buf and len(buf) + len(piece) + 1 > self.chunk_size:
                chunks.append(buf.strip())
                buf = piece
            else:
                buf = buf + piece
        if buf.strip():
            chunks.append(buf.strip())
        return chunks


# ---------------------------------------------------------------------------
# 工厂
# ---------------------------------------------------------------------------

_CHUNKER_NAMES = {
    "identity": IdentityChunking,
    "regex": RegexChunking,
    "sentence": SentenceChunking,
    "fixed": FixedSizeChunking,
    "fixed_size": FixedSizeChunking,
    "sliding": SlidingWindowChunking,
    "sliding_window": SlidingWindowChunking,
    "overlapping": OverlappingWindowChunking,
    "recursive": RecursiveChunking,
}


def create_chunker(name: str = "fixed", **kwargs) -> ChunkingStrategy:
    """创建分块器。"""
    key = (name or "fixed").lower()
    cls = _CHUNKER_NAMES.get(key)
    if cls is None:
        raise ValueError(f"未知分块策略: {name}，可选: {sorted(set(_CHUNKER_NAMES))}")
    return cls(**kwargs)
