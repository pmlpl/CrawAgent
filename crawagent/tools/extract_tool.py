"""提取工具 — BeautifulSoup4 + lxml 从 HTML 中提取结构化内容

硬约束：Supervisor must automatically upgrade to browser mode if extract phase fails with confidence <60
实现：提取完成后调用 confidence.evaluate_confidence 打分，<60 分时在返回值末尾加 [LOW CONFIDENCE] 标记，
由 Agent 看到标记后自动切 browse_and_crawl 自升级。

HTML→Markdown：基于 html2text 按 DOM 树整体转换（替代旧的 find_all 平铺抓取），
保留标题层级 / 代码块缩进 / 列表标记 / 引用 / 表格 / 行内链接与行内代码；
转换后做空白归一化块级去重，避免被 <span>/空白包裹的重复段落逃过去重。
"""
import re

import html2text
from bs4 import BeautifulSoup
from langchain_core.tools import tool

from crawagent.tools.confidence import evaluate_confidence


def _make_converter() -> html2text.HTML2Text:
    """构造 html2text 转换器：不折行、``` 围栏代码块、- 列表、行内链接。"""
    h2t = html2text.HTML2Text()
    h2t.body_width = 0              # 禁止自动折行，保持段落与代码原样
    h2t.backquote_code_style = True # 块级代码用 ``` 围栏并保留原始缩进
    h2t.ul_item_mark = "-"          # 无序列表用 - 标记
    h2t.single_line_break = False
    return h2t


def _extract_code_langs(root) -> list[str]:
    """按文档顺序提取每个 <pre> 内 <code> 的语言标注（class="language-x"/"lang-x"）。"""
    langs = []
    for pre in root.find_all("pre"):
        code = pre.find("code")
        lang = ""
        for cls in (code.get("class") or []) if code else []:
            for prefix in ("language-", "lang-"):
                if cls.startswith(prefix):
                    lang = cls[len(prefix):]
                    break
            if lang:
                break
        langs.append(lang)
    return langs


def _apply_code_langs(md: str, langs: list[str]) -> str:
    """html2text 输出裸 ``` 围栏，按顺序把 <pre> 的语言标注回填到开围栏上。

    围栏配对结构 "A```C1```B" → split 得 ["A", "C1", "B"]：
    开围栏位于偶数段之后，语言加在奇数段（代码内容）的开头，即得 "```python"。"""
    if not langs:
        return md
    parts = md.split("```")
    for k, lang in enumerate(langs, start=1):
        idx = 2 * k - 1
        if lang and idx < len(parts) - 1:  # 最后一段是闭围栏后的文本，非代码内容
            parts[idx] = lang + parts[idx]
    return "```".join(parts)


def _norm(text: str) -> str:
    """去空白 + 小写，用于去重与标题相似度比对。"""
    return re.sub(r"\s+", "", text).lower()


def _dedupe_blocks(md: str) -> str:
    """按空行分块做空白归一化去重（span/空白包裹的重复段落不再逃逸）。

    ``` 围栏内与围栏行本身不参与去重，避免把孤立的闭合围栏当重复块删掉。"""
    seen: set[str] = set()
    out: list[str] = []
    in_code = False
    fence_re = re.compile(r"^\s*```", re.MULTILINE)
    for block in md.split("\n\n"):
        if in_code or fence_re.search(block):
            out.append(block)
            # 块内围栏行数的奇偶决定是否跨越围栏边界
            in_code = in_code != (len(fence_re.findall(block)) % 2 == 1)
            continue
        key = _norm(block)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(block)
    return "\n\n".join(out)


def _normalize_headings(root) -> None:
    """站点常把 <h1> 留给站名、正文从 <h2> 开始（如 CSDN）。

    正文容器内无 h1 且有 h2 时整体升一级，保证 md 以 # 级文章标题开头。"""
    if root.find("h1") or not root.find("h2"):
        return
    mapping = {"h2": "h1", "h3": "h2", "h4": "h3", "h5": "h4", "h6": "h5"}
    for h in root.find_all(list(mapping)):
        h.name = mapping[h.name]


def _reconcile_title(md: str, title: str) -> str:
    """正文首个标题与 <title> 高度相似（<title> 常带 '-CSDN博客' 等网站后缀）时，
    用更干净的正文标题替换 title，避免保存 md 时出现重复标题。"""
    m = re.search(r"^#{1,6}\s+(.+?)\s*$", md, re.MULTILINE)
    if not m:
        return title
    heading = m.group(1)
    nh, ntitle = _norm(heading), _norm(title)
    if nh and ntitle and (nh in ntitle or ntitle in nh):
        return heading
    return title


@tool
def extract_content(html: str, focus: str = "") -> str:
    """从 HTML 里抽取结构化正文，并输出成**格式良好的 Markdown**。

    处理内容：提取页面标题 + 把正文主体转成 Markdown，保留
    标题层级（#/##/###）、代码块围栏及原始缩进、列表标记（- / 1.）、
    引用块、表格、行内链接 [文字](url)、行内代码、加粗/斜体。
    重复段落（包括被空白/span 包裹的变体）会自动去重。

    如果提供了 focus 参数，只返回包含该关键词的正文块。

    抽取结果末尾会自动附上 Supervisor 置信度评分：
    - 评分 ≥60 →  "[置信度: score=X]"（可信，直接使用）
    - 评分 <60  →  "[低置信度: score=X, 原因=...]"
      **低置信度出现时（这是硬规则）：调用方必须改用 browse_and_crawl
      通过浏览器重新拉页面再做一次抽取，绝对不能拿低置信结果直接交付用户。**

    参数：
        html: 页面 HTML 源码
        focus: 可选关键词过滤；为空则抽取整段正文

    返回：
        Markdown 文本 —— 首行 "Title: <标题>"，之后 Markdown 正文，
        末尾附置信度标记 "[置信度: score=X]" 或 "[低置信度: ...]"。
    """
    soup = BeautifulSoup(html, "lxml")

    # Extract title
    title = soup.title.string.strip() if soup.title and soup.title.string else "Untitled"

    # Remove scripts and styles
    for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
        tag.decompose()

    # 主内容容器：优先 <article> → <main> → <body>，避免侧栏噪音；
    # 容器内容过短（<200 字符）时回退全文
    root = soup.find("article") or soup.find("main") or soup.body or soup
    if len(root.get_text(strip=True)) < 200:
        root = soup.body or soup

    _normalize_headings(root)
    md = _make_converter().handle(str(root)).strip()
    md = _apply_code_langs(md, _extract_code_langs(root))
    # lxml 序列化会在 code 尾部引入空白，清掉围栏行尾空格
    md = re.sub(r"^(```[\w+-]*)[ \t]+$", r"\1", md, flags=re.MULTILINE)
    title = _reconcile_title(md, title)
    md = _dedupe_blocks(md)

    # focus 过滤：按空行分块，只保留含关键词的块（代码块整体保留不切碎）
    if focus:
        kw = focus.lower()
        md = "\n\n".join(b for b in md.split("\n\n") if kw in b.lower())

    result_str = f"Title: {title}\n\n{md}"

    # Supervisor 置信度评估（硬约束：<60 触发浏览器模式自升级）
    conf = evaluate_confidence(content=md, title=title)
    result_str += conf.format_marker()

    return result_str
