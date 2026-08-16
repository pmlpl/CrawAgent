"""提取工具 — BeautifulSoup4 + lxml 从 HTML 中提取结构化内容

硬约束：Supervisor must automatically upgrade to browser mode if extract phase fails with confidence <60
实现：提取完成后调用 confidence.evaluate_confidence 打分，<60 分时在返回值末尾加 [LOW CONFIDENCE] 标记，
由 Agent 看到标记后自动切 browse_and_crawl 自升级。
"""
from langchain_core.tools import tool
from bs4 import BeautifulSoup
from crawagent.tools.confidence import evaluate_confidence


@tool
def extract_content(html: str, focus: str = "") -> str:
    """Extract structured content from HTML, including title, body text, and links.

    Automatically extracts: title, body text, all links, and image URLs.
    If the focus parameter is provided, only returns paragraphs containing that keyword.

    After extraction, a Supervisor confidence score is appended:
    - [CONFIDENCE: score=X] if >=60 (trusted, use this result)
    - [LOW CONFIDENCE: score=X, reasons=...] if <60
      When LOW CONFIDENCE appears, the caller (agent) MUST switch to browse_and_crawl
      to re-fetch the page via a browser and re-run extraction (hard rule).

    Args:
        html: The HTML source of the webpage
        focus: Optional keyword to filter results. If empty, extracts all body text

    Returns:
        Plain text with labeled sections + confidence marker at the end.
        Format:
        "Title: <title>"
        "Body (<N> paragraphs): <body text>"
        "Links (<N> total): [text](url) per line"
        "[CONFIDENCE: score=X]" or "[LOW CONFIDENCE: score=X, reasons=...]"
    """
    soup = BeautifulSoup(html, "lxml")

    # Extract title
    title = soup.title.string.strip() if soup.title and soup.title.string else "Untitled"

    # Remove scripts and styles
    for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
        tag.decompose()

    # Extract body text
    paragraphs = []
    for p in soup.find_all(["p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6", "article"]):
        text = p.get_text(strip=True)
        if text and len(text) > 10:  # Filter short fragments
            if focus:
                if focus.lower() in text.lower():
                    paragraphs.append(text)
            else:
                paragraphs.append(text)

    # Deduplicate
    seen = set()
    unique_paragraphs = []
    for p in paragraphs:
        if p not in seen:
            seen.add(p)
            unique_paragraphs.append(p)

    body_text = "\n".join(unique_paragraphs)

    # Extract links
    links = []
    for a in soup.find_all("a", href=True):
        link_text = a.get_text(strip=True)
        if link_text and a["href"].startswith("http"):
            links.append(f"[{link_text}]({a['href']})")

    # Assemble result
    result_parts = [f"Title: {title}", f"\nBody ({len(unique_paragraphs)} paragraphs):\n{body_text}"]
    if links:
        result_parts.append(f"\nLinks ({len(links)} total):\n" + "\n".join(links[:30]))

    result_str = "\n".join(result_parts)

    # Supervisor 置信度评估（硬约束：<60 触发浏览器模式自升级）
    conf = evaluate_confidence(content=body_text, title=title)
    result_str += conf.format_marker()

    return result_str
