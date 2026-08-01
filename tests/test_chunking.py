"""P2-4 分块策略测试：7 种策略。"""

from crawagent.core.chunking import (
    FixedSizeChunking,
    IdentityChunking,
    OverlappingWindowChunking,
    RecursiveChunking,
    RegexChunking,
    SentenceChunking,
    SlidingWindowChunking,
    create_chunker,
)


TEXT = ("这是一个用于测试分块的示例文本。" * 40) + "\n\n第二段落。\n\n第三段落。"


def test_identity():
    assert IdentityChunking().chunk(TEXT) == [TEXT]


def test_regex():
    chunks = RegexChunking([r"\n\n"]).chunk("第一段\n\n第二段\n\n第三段")
    assert chunks == ["第一段", "第二段", "第三段"]


def test_sentence():
    chunks = SentenceChunking().chunk("第一句。第二句！第三句？\n第四句。")
    assert len(chunks) >= 3
    assert "第一句。" in chunks


def test_fixed_size():
    chunks = FixedSizeChunking(chunk_size=50, overlap=0).chunk(TEXT)
    assert all(len(c) <= 50 for c in chunks)
    assert "".join(chunks) == TEXT


def test_sliding_window():
    chunks = SlidingWindowChunking(window_size=100, step=50).chunk(TEXT)
    assert len(chunks) > 1
    assert all(len(c) <= 100 for c in chunks)


def test_overlapping_window():
    chunks = OverlappingWindowChunking(window_size=100, overlap=30).chunk(TEXT)
    assert len(chunks) >= 1
    assert all(len(c) >= 1 for c in chunks)


def test_recursive_chunking_respects_size():
    long_text = "。".join(f"句子编号{i}内容" for i in range(500))
    chunks = RecursiveChunking(chunk_size=200).chunk(long_text)
    assert len(chunks) > 1
    assert all(len(c) <= 210 for c in chunks)
    assert "".join(chunks).replace("\n", "") == long_text


def test_factory():
    assert isinstance(create_chunker("identity"), IdentityChunking)
    assert isinstance(create_chunker("fixed"), FixedSizeChunking)
    assert isinstance(create_chunker("recursive"), RecursiveChunking)


def test_empty_text():
    assert FixedSizeChunking().chunk("") == []
    assert SentenceChunking().chunk("") == []
    assert RecursiveChunking().chunk("") == []
