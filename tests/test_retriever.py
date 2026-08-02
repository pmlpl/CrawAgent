"""检索器测试：工厂行为与向量占位清理验证。"""

import pytest

from crawagent.core.retriever import SQLiteFTS5Retriever, create_retriever


def test_create_retriever_default_sqlite_fts5(tmp_path):
    retriever = create_retriever(db_path=str(tmp_path / "r.db"))
    assert isinstance(retriever, SQLiteFTS5Retriever)
    retriever.close()


def test_fts5_add_and_search(tmp_path):
    retriever = SQLiteFTS5Retriever(db_path=str(tmp_path / "r.db"))
    retriever.add([
        {"id": "1", "title": "Example", "content": "this is a test document for retrieval", "url": "https://example.com"},
    ])
    results = retriever.search("test")
    assert isinstance(results, list)
    assert len(results) >= 1
    assert results[0].id == "1"
    retriever.close()


def test_create_retriever_unsupported_backend_raises():
    # 占位 VectorRetriever 已移除：未知后端应给出明确错误
    with pytest.raises(ValueError, match="sqlite_fts5"):
        create_retriever(backend="vector")
