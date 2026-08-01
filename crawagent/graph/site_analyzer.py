from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Dict, List, Optional

from playwright.async_api import async_playwright, Browser, Page, Response
from pydantic import BaseModel, Field

from crawagent.core.models import SiteAnalysis, Selectors, Pagination, PageType, DataSource, AntiBotSign
from crawagent.llm.factory import get_llm
from crawagent.config.settings import get_settings
from crawagent.graph.prompts.site_analyzer import SITE_ANALYZER_PROMPT


class NetworkCapture:
    """网络请求捕获器"""

    def __init__(self):
        self.responses: List[Dict] = []
        self._lock = asyncio.Lock()

    async def on_response(self, response: Response) -> None:
        if not response.ok:
            return
        content_type = response.headers.get("content-type", "")
        if "json" not in content_type and "javascript" not in content_type:
            return

        try:
            body = await response.json()
        except Exception:
            try:
                body = await response.text()
            except Exception:
                return

        async with self._lock:
            self.responses.append({
                "url": response.url,
                "method": response.request.method,
                "status": response.status,
                "content_type": content_type,
                "body_preview": str(body)[:2000],
                "is_json": isinstance(body, (dict, list)),
            })

    def get_summary(self, limit: int = 30) -> List[Dict]:
        """返回摘要给 LLM"""
        # 优先返回 JSON 响应
        json_resps = [r for r in self.responses if r["is_json"]]
        other_resps = [r for r in self.responses if not r["is_json"]]
        return (json_resps + other_resps)[:limit]


async def extract_dom_snippet(page: Page, max_chars: int = 50000) -> str:
    """提取关键 DOM 片段"""
    import re
    selectors = [
        "main", "[role='main']", ".content", ".container", 
        ".list", ".feed", ".video-list", ".article-list",
        "body"
    ]
    
    for sel in selectors:
        try:
            element = await page.query_selector(sel)
            if element:
                html = await element.inner_html()
                if len(html) > 500:
                    return html[:max_chars]
        except Exception:
            continue
    
    body = await page.query_selector("body")
    if body:
        html = await body.inner_html()
        return html[:max_chars]
    return ""


async def analyze_site_simple(url: str) -> SiteAnalysis:
    """
    简化版站点分析（不使用子图，直接执行一次完整流程）
    适用于不需要迭代的简单场景。
    """
    capture = NetworkCapture()

    async with async_playwright() as p:
        browser: Browser = await p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled", "--no-proxy-server"]
        )

        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            viewport={"width": 1920, "height": 1080},
        )

        page: Page = await context.new_page()
        page.on("response", capture.on_response)

        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            try:
                await page.wait_for_load_state("networkidle", timeout=10000)
            except Exception:
                pass
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await asyncio.sleep(1)

            title = await page.title()
            dom_snippet = await extract_dom_snippet(page, max_chars=4000)
            network_summary = capture.get_summary()

        finally:
            await context.close()
            await browser.close()

    # LLM 推理
    llm = get_llm()

    prompt = SITE_ANALYZER_PROMPT.format(
        url=url,
        title=title,
        dom_snippet=dom_snippet,
        network_summary=json.dumps(network_summary, ensure_ascii=False, indent=2),
    )

    try:
        response = await llm.ainvoke([
            {"role": "system", "content": "你是网页结构分析专家，输出严格 JSON。"},
            {"role": "user", "content": prompt}
        ])
        content = response.content
        import re
        json_match = re.search(r'\{.*\}', content, re.DOTALL)
        if json_match:
            data = json.loads(json_match.group())
        else:
            data = json.loads(content)
    except Exception as e:
        return SiteAnalysis(
            page_type=PageType.UNKNOWN,
            confidence=0.0,
            raw_evidence={"error": str(e)},
        )

    return parse_llm_output(data, url, network_summary)


def parse_llm_output(data: Dict, url: str, network_summary: List[Dict]) -> SiteAnalysis:
    """解析 LLM 输出为 SiteAnalysis"""
    page_type = PageType(data.get("page_type", "unknown"))
    
    # 选择器（LLM 可能显式传 None，需统一转为空字符串）
    selectors_data = data.get("selectors", {}) or {}
    selectors = Selectors(
        list_container=selectors_data.get("list_container") or "",
        item=selectors_data.get("item") or "",
        title=selectors_data.get("title") or "",
        url=selectors_data.get("url") or "",
        extra=selectors_data.get("extra") or {},
    )
    
    # 数据源
    data_source_str = data.get("data_source", "dom")
    data_source = DataSource(data_source_str) if data_source_str in DataSource.__members__.values() else DataSource.DOM
    
    # API 端点
    api_endpoint = data.get("api_endpoint")
    api_method = data.get("api_method", "GET")
    api_headers = data.get("api_headers", {})
    api_params = data.get("api_params", {})
    
    # 分页
    pagination_data = data.get("pagination", {})
    pagination = Pagination(
        type=pagination_data.get("type", "none"),
        param=pagination_data.get("param"),
        max_page_selector=pagination_data.get("max_page_selector"),
        cursor_selector=pagination_data.get("cursor_selector"),
        has_next_selector=pagination_data.get("has_next_selector"),
    )
    
    # 反爬迹象
    anti_bot = []
    for sign in data.get("anti_bot_signs", []):
        if sign in AntiBotSign.__members__.values():
            anti_bot.append(AntiBotSign(sign))
    
    confidence = float(data.get("confidence", 0.5))
    
    return SiteAnalysis(
        page_type=page_type,
        data_source=data_source,
        selectors=selectors,
        api_endpoint=api_endpoint,
        api_method=api_method,
        api_headers=api_headers,
        api_params=api_params,
        pagination=pagination,
        anti_bot_signs=anti_bot,
        confidence=confidence,
        raw_evidence={
            "url": url,
            "network_summary": network_summary,
        },
    )


# ==================== LangGraph 子图 ====================

def build_site_analyzer_graph():
    """构建 SiteAnalyzer 子图（内部循环：发现端点 -> 理解 -> 再发现）"""
    from langgraph.graph import StateGraph, END
    from langgraph.checkpoint.memory import MemorySaver
    
    class AnalyzerState(TypedDict):
        url: str
        iteration: int
        max_iterations: int
        current_analysis: Optional[SiteAnalysis]
        discovered_endpoints: List[Dict]
        done: bool
        dom_snippet: str
        page_title: str
        network_summary: List[Dict]
    
    graph = StateGraph(AnalyzerState)
    
    # 节点
    graph.add_node("load_and_capture", load_and_capture_node)
    graph.add_node("llm_analyze", llm_analyze_node)
    graph.add_node("validate_selectors", validate_selectors_node)
    graph.add_node("decide_continue", decide_continue_node)
    
    graph.set_entry_point("load_and_capture")
    graph.add_edge("load_and_capture", "llm_analyze")
    graph.add_edge("llm_analyze", "validate_selectors")
    graph.add_edge("validate_selectors", "decide_continue")
    
    graph.add_conditional_edges(
        "decide_continue",
        lambda s: "continue" if not s["done"] and s["iteration"] < s["max_iterations"] else "done",
        {"continue": "load_and_capture", "done": END}
    )
    
    return graph.compile()


async def load_and_capture_node(state: AnalyzerState) -> AnalyzerState:
    """加载页面并捕获网络"""
    url = state["url"]
    capture = NetworkCapture()
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-proxy-server"])
        context = await browser.new_context()
        page = await context.new_page()
        page.on("response", capture.on_response)
        
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        try:
            await page.wait_for_load_state("networkidle", timeout=10000)
        except Exception:
            pass
        
        # 滚动
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await asyncio.sleep(1)
        
        state["dom_snippet"] = await extract_dom_snippet(page, max_chars=4000)
        state["network_summary"] = capture.get_summary()
        state["page_title"] = await page.title()
        
        await context.close()
        await browser.close()
    
    state["iteration"] += 1
    return state


async def llm_analyze_node(state: AnalyzerState) -> AnalyzerState:
    """LLM 分析"""
    llm = get_llm()
    
    prompt = SITE_ANALYZER_PROMPT.format(
        url=state["url"],
        title=state["page_title"],
        dom_snippet=state["dom_snippet"],
        network_summary=json.dumps(state["network_summary"], ensure_ascii=False, indent=2),
    )
    
    response = await llm.ainvoke([
        {"role": "system", "content": "你是网页结构分析专家，输出严格 JSON。"},
        {"role": "user", "content": prompt}
    ])
    
    import re
    json_match = re.search(r'\{.*\}', response.content, re.DOTALL)
    data = json.loads(json_match.group()) if json_match else json.loads(response.content)
    
    analysis = parse_llm_output(data, state["url"], state["network_summary"])
    state["current_analysis"] = analysis
    
    # 收集发现的端点
    if analysis.api_endpoint:
        state["discovered_endpoints"].append({
            "url": analysis.api_endpoint,
            "method": analysis.api_method,
            "params": analysis.api_params,
        })
    
    return state


async def validate_selectors_node(state: AnalyzerState) -> AnalyzerState:
    """验证选择器是否有效"""
    analysis = state["current_analysis"]
    if not analysis or not analysis.selectors.list_container:
        return state
    
    url = state["url"]
    selector = analysis.selectors.list_container
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-proxy-server"])
        context = await browser.new_context()
        page = await context.new_page()
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=15000)
            count = await page.locator(selector).count()
            analysis.raw_evidence["selector_validation"] = {
                "selector": selector,
                "matched_count": count,
                "valid": count > 0,
            }
        except Exception as e:
            analysis.raw_evidence["selector_validation"] = {"error": str(e)}
        finally:
            await context.close()
            await browser.close()
    
    return state


async def decide_continue_node(state: AnalyzerState) -> AnalyzerState:
    """决定是否继续迭代"""
    # 如果已有高置信度分析且验证通过，结束
    analysis = state.get("current_analysis")
    if analysis and analysis.confidence > 0.8:
        validation = analysis.raw_evidence.get("selector_validation", {})
        if validation.get("valid", False):
            state["done"] = True
            return state
    
    # 如果发现新端点，继续分析该端点
    if state["discovered_endpoints"]:
        last_endpoint = state["discovered_endpoints"][-1]
        state["url"] = last_endpoint["url"]
        state["done"] = False
        return state
    
    state["done"] = True
    return state


# ==================== 导出函数 ====================

async def analyze_site(url: str) -> SiteAnalysis:
    """外部调用入口：分析单个站点"""
    # 简化版：直接运行一次完整流程
    state = {
        "url": url,
        "iteration": 0,
        "max_iterations": 3,
        "current_analysis": None,
        "discovered_endpoints": [],
        "done": False,
    }
    
    # 运行简化流程
    state = await load_and_capture_node(state)
    state = await llm_analyze_node(state)
    state = await validate_selectors_node(state)
    
    return state["current_analysis"] or SiteAnalysis(page_type=PageType.UNKNOWN)