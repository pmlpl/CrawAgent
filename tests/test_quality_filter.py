"""save_record 五关质量过滤回归测试（变更 009）。

覆盖：长度/信噪/去重/结构/营销 各关的淘汰与放行，rejected_records 落表，
save_record 集成返回 [REJECTED]/[DUPLICATE]/Saved。
"""
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture()
def db(tmp_path, monkeypatch):
    """隔离 DB + 注入 session。"""
    dbp = tmp_path / "q.db"
    import crawagent.tools.save_tool as st
    import crawagent.tools.query_tool as qt
    monkeypatch.setattr(st, "_get_db_path", lambda: str(dbp))
    monkeypatch.setattr(qt, "_get_db_path", lambda: str(dbp))
    st._init_db()
    st.set_current_session("s1")
    return st, dbp


# ── 各关淘汰 ──

def test_gate_length_rejects_short(db):
    st, _ = db
    res = st.save_record.func(url="http://x", title="短", content="太短了" * 5)  # <200
    assert "[REJECTED]" in res and "长度" in res


def test_gate_structure_rejects_no_title(db):
    st, _ = db
    long = "一段没有标题也没有结构的内容" * 30  # ≥200 字但单行无结构
    res = st.save_record.func(url="http://x", title="", content=long)
    assert "[REJECTED]" in res and "无标题" in res  # 关④先判无标题


def test_gate_structure_rejects_single_line_blob(db):
    st, _ = db
    # 有标题、≥200 字、但全挤一行无段落/列表/标题标记 → 无结构
    blob = "这是被塞进一整行的SEO垃圾内容用于骗过长度检查" * 10
    res = st.save_record.func(url="http://x", title="有标题", content=blob)
    assert "[REJECTED]" in res and "结构" in res


def test_gate_noise_rejects_low_ratio(db):
    st, _ = db
    # content ≥200（过①）有结构（过④），但 html 远大于正文 → 信噪比 <15%
    content = "# 噪声页\n\n" + "这是正文段落内容用于测试噪声。\n\n" * 12  # ≥200 + 结构
    html = "<html><body>" + ("<div>导航冗余内容</div>" * 800) + "</body></html>"  # 远大于正文
    res = st.save_record.func(url="http://x", title="噪", content=content, html=html)
    assert "[REJECTED]" in res and "信噪" in res


def test_gate_spam_link_ratio_rejects(db):
    st, _ = db
    # content ≥200 有结构（过①④），html 全是 <a> → 链接文字比 >25%
    content = "# 索引页\n\n" + "这是正文段落内容用于撑大正文长度测试链接比。\n\n" * 40
    html = "<html><body>" + ("<a>链接文字</a>" * 200) + "</body></html>"
    res = st.save_record.func(url="http://x", title="索引页", content=content, html=html)
    assert "[REJECTED]" in res and "链接" in res


def test_gate_spam_marketing_rejects(db):
    st, _ = db
    # 营销词 ≥3 + 代码密度低 + 有结构（过①④）→ 营销页
    content = "# 营销页\n\n" + ("限时优惠 立即购买 免费领取 扫码订阅，营销文案无代码标记。\n\n" * 12)
    res = st.save_record.func(url="http://x", title="营销页", content=content)
    assert "[REJECTED]" in res and "营销" in res


# ── 放行 + 去重 ──

def test_good_content_saved(db):
    st, dbp = db
    content = "# 标题\n\n## 小节\n\n这是足够长的优质正文内容，包含段落结构和足够的字数。" * 8
    res = st.save_record.func(url="http://good", title="好内容", content=content)
    assert "Saved successfully" in res
    con = sqlite3.connect(str(dbp))
    n = con.execute("SELECT count(*) FROM crawl_records").fetchone()[0]
    assert n == 1


def test_dedup_skips_identical(db):
    st, _ = db
    content = "# 标题\n\n这是足够长的优质正文内容，有结构有段落，字数也充足。" * 8  # ≥200
    r1 = st.save_record.func(url="http://a", title="T", content=content)
    assert "Saved successfully" in r1
    r2 = st.save_record.func(url="http://b", title="T2", content=content)
    assert "[DUPLICATE]" in r2


def test_rejection_recorded_in_rejected_table(db):
    st, dbp = db
    st.save_record.func(url="http://bad", title="短", content="短")  # 关①拒
    con = sqlite3.connect(str(dbp))
    n = con.execute("SELECT count(*) FROM rejected_records").fetchone()[0]
    assert n == 1
    reason = con.execute("SELECT reason FROM rejected_records").fetchone()[0]
    assert "长度" in reason


def test_content_md5_stored(db):
    st, dbp = db
    content = "# 标题\n\n优质正文，足够长，结构清晰。" * 12  # ≥200
    st.save_record.func(url="http://x", title="T", content=content)
    con = sqlite3.connect(str(dbp))
    md5 = con.execute("SELECT content_md5 FROM crawl_records").fetchone()[0]
    assert md5 and len(md5) == 32
