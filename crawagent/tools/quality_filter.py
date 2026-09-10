"""入库质量过滤 — save_record 的五道闸门（变更 009）。

不过关不入库：淘汰记录进 rejected_records 表，save_record 返回 [REJECTED] +
淘汰原因（让 LLM 知道为什么被拒、下一手该怎么改）。

五关：
  ① 长度   正文长度 < MIN_CONTENT_LEN                    → 淘汰
  ② 信噪   正文/正文HTML < MIN_TEXT_HTML_RATIO（有 html 才判）→ 淘汰
  ③ 去重   md5(前 DEDUP_HEAD_CHARS 字) 已存在            → 跳过/更新
  ④ 价值   无标题 或 无结构（整页挤一行无段落/列表/标题）  → 淘汰
  ⑤ 类型   链接文字比 > MAX_LINK_TEXT_RATIO（有 html 才判）→ 索引页淘汰；
           营销词 ≥ MARKETING_WORD_HITS 且代码密度 < MIN_CODE_DENSITY_PER_K/千字 → 营销页淘汰

阈值全提成本文件顶部常量，调优只改这里。
"""
from __future__ import annotations

import hashlib
import re
import sqlite3

# ── 阈值常量（调优只改这里） ──────────────────────────────────────
MIN_CONTENT_LEN = 200          # 关①：正文最小长度（字符）
MIN_TEXT_HTML_RATIO = 0.15     # 关②：可见正文长度 / 原始 HTML 长度 下限
DEDUP_HEAD_CHARS = 500         # 关③：去重指纹取正文前 N 字
MAX_LINK_TEXT_RATIO = 0.25     # 关⑤：链接文字占比上限（索引/导航页特征）
MARKETING_WORD_HITS = 3        # 关⑤：营销词命中（不同词）数下限
MIN_CODE_DENSITY_PER_K = 2.0   # 关⑤：每千字代码标记数下限（低于它 + 营销词 → 营销页）

# 营销词表（中英）：命中 ≥3 个不同词 = 营销页嫌疑
_MARKETING_WORDS = (
    "限时", "优惠", "立即购买", "免费领取", "扫码", "订阅", "注册领取", "秒杀",
    "discount", "limited offer", "sign up now", "buy now", "subscribe now",
    "free trial", "click here", "special offer",
)

# 代码标记（判定代码密度）：命中即 +1
_CODE_MARKERS = (
    "def ", "class ", "import ", "function", "return ", "const ", "let ", "var ",
    "=>", "{", "}", ";", "</", "<?", "#!/", "pip install", "npm ", "SELECT ",
)


def content_fingerprint(content: str) -> str:
    """去重指纹：正文前 N 字的 md5。"""
    return hashlib.md5(content[:DEDUP_HEAD_CHARS].encode("utf-8", "replace")).hexdigest()


def _code_density_per_k(content: str) -> float:
    """每千字代码标记数。"""
    if not content:
        return 0.0
    hits = sum(content.count(m) for m in _CODE_MARKERS)
    return hits * 1000.0 / max(len(content), 1)


def _link_text_ratio(html: str, content: str) -> float | None:
    """链接文字占比：HTML 里 <a> 内文字总长 / 可见正文总长。无 html 返回 None。"""
    if not html:
        return None
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        link_chars = sum(len(a.get_text(strip=True)) for a in soup.find_all("a"))
        for tag in soup(["script", "style"]):
            tag.decompose()
        visible = len(soup.get_text(strip=True))
        if visible <= 0:
            return None
        return link_chars / visible
    except Exception:
        return None


def gate_length(content: str) -> str | None:
    """关①：正文长度。"""
    if len(content or "") < MIN_CONTENT_LEN:
        return f"正文长度 {len(content or '')} < {MIN_CONTENT_LEN} 字"
    return None


def gate_noise(content: str, html: str) -> str | None:
    """关②：信噪比（可见正文 / 原始 HTML）。无 html 跳过。"""
    if not html:
        return None
    if len(html) <= 0:
        return None
    ratio = len(content or "") / len(html)
    if ratio < MIN_TEXT_HTML_RATIO:
        return f"信噪比 {ratio:.1%} < {MIN_TEXT_HTML_RATIO:.0%}（正文 {len(content)} / HTML {len(html)}）"
    return None


def gate_dedup(conn: sqlite3.Connection, content: str) -> tuple[str | None, int | None]:
    """关③：md5(前 N 字) 查重。返回 (reason, existing_id)。"""
    fp = content_fingerprint(content or "")
    row = conn.execute(
        "SELECT id, url FROM crawl_records WHERE content_md5 = ? LIMIT 1", (fp,)
    ).fetchone()
    if row:
        return f"与已有记录 ID {row[0]}（{row[1]}）前 {DEDUP_HEAD_CHARS} 字相同", row[0]
    return None, None


def gate_structure(title: str, content: str) -> str | None:
    """关④：无标题 或 无结构（内容挤在一行且无段落/列表/标题标记）。"""
    if not (title or "").strip():
        return "无标题"
    lines = [l.strip() for l in (content or "").splitlines() if l.strip()]
    has_marker = any(
        l.startswith(("#", "-", "*", "+")) or re.match(r"^\d+[.、)]", l)
        for l in lines
    )
    if len(lines) < 3 and not has_marker:
        return f"无结构（仅 {len(lines)} 行且无段落/列表/标题标记，典型 SEO 垃圾页）"
    return None


def gate_spam(content: str, html: str) -> str | None:
    """关⑤：链接文字比超限（索引页）或 营销词多且代码密度低（营销页）。"""
    ratio = _link_text_ratio(html, content)
    if ratio is not None and ratio > MAX_LINK_TEXT_RATIO:
        return f"链接文字比 {ratio:.0%} > {MAX_LINK_TEXT_RATIO:.0%}（索引/导航页）"

    lower = (content or "").lower()
    marketing_hits = sum(1 for w in _MARKETING_WORDS if w in lower)
    if marketing_hits >= MARKETING_WORD_HITS:
        density = _code_density_per_k(content or "")
        if density < MIN_CODE_DENSITY_PER_K:
            return (
                f"营销词命中 {marketing_hits} 个且代码密度 {density:.1f}/千字 "
                f"< {MIN_CODE_DENSITY_PER_K}（营销页）"
            )
    return None


def run_quality_gates(
    conn: sqlite3.Connection, url: str, title: str, content: str, html: str = ""
) -> tuple[bool, str, str | None]:
    """依次跑五关。

    Returns:
        (passed, fingerprint, reject_reason)
        passed=True → 可入库；reject_reason=None
        passed=False → reject_reason 给出淘汰原因（fingerprint 仍返回，便于落 rejected）
    """
    fp = content_fingerprint(content or "")
    for gate in (
        lambda: gate_length(content),
        lambda: gate_noise(content, html),
        lambda: gate_structure(title, content),
        lambda: gate_spam(content, html),
    ):
        reason = gate()
        if reason:
            return False, fp, reason
    # 关③去重单独跑（需要 conn，且语义是"跳过"而非"淘汰"）
    reason, _existing = gate_dedup(conn, content)
    if reason:
        return False, fp, reason
    return True, fp, None


def record_rejection(
    conn: sqlite3.Connection, url: str, title: str, reason: str,
    content: str = "", fingerprint: str = "",
) -> None:
    """淘汰记录落 rejected_records 表（审计 + 观察阈值效果）。"""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS rejected_records ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "url TEXT, title TEXT, reason TEXT, fingerprint TEXT, "
        "content_preview TEXT, created_at TEXT NOT NULL, session_id TEXT DEFAULT '')"
    )
    conn.execute(
        "INSERT INTO rejected_records (url, title, reason, fingerprint, content_preview, created_at, session_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (url, title, reason, fingerprint, (content or "")[:200],
         __import__("datetime").datetime.now().isoformat(),
         __import__("crawagent.tools.save_tool", fromlist=["_current_session"])._current_session.get("")),
    )
