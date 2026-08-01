from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Any, Dict, List, Optional, TypedDict

from langchain_core.messages import BaseMessage, HumanMessage, AIMessage, ToolMessage
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver
from pydantic import BaseModel, Field

from crawagent.core.models import (
    AgentState, CrawlJob, CrawlPlan, SiteAnalysis, 
    CrawlResult, ExtractedItem, PageType, CrawlJobStatus
)
from crawagent.core.fetcher import Fetcher
from crawagent.core.frontier import SQLiteFrontier
from crawagent.core.extractor import DEFAULT_EXTRACTOR
from crawagent.core.extractor import create_dynamic_schema
from crawagent.graph.site_analyzer import analyze_site
from crawagent.graph.anti_bot import handle_anti_bot_challenge, StrategyUpgrade
from crawagent.llm.factory import get_llm
from crawagent.llm.prompts import (
    CLASSIFY_PROMPT, PLAN_PROMPT, DECIDE_PROMPT,
    EXTRACTION_SCHEMA_PROMPT
)
from loguru import logger


# ==================== 辅助函数 ====================

def _add_log(state: AgentState, level: str, message: str):
    """给 state 添加日志，同时输出到 logger"""
    log_entry = {"level": level, "message": message}
    if "logs" not in state or state["logs"] is None:
        state["logs"] = []
    state["logs"].append(log_entry)
    logger.log(level, message)


# ==================== 节点函数 ====================

async def classify_node(state: AgentState) -> AgentState:
    """分类页面类型"""
    _add_log(state, "INFO", "【1/5】正在分析任务类型...")
    llm = get_llm()
    user_input = state["user_input"]
    seed_urls = state.get("seed_urls", [])
    
    prompt = CLASSIFY_PROMPT.format(
        user_input=user_input,
        seed_urls=seed_urls,
    )
    
    _add_log(state, "INFO", f"调用 LLM 分类中... 种子URL: {len(seed_urls)} 个")
    response = await get_llm().ainvoke([
        {"role": "system", "content": "你是网页分类专家，输出 JSON。"},
        {"role": "user", "content": prompt}
    ])
    
    import re
    json_match = re.search(r'\{.*\}', response.content, re.DOTALL)
    data = json.loads(json_match.group()) if json_match else json.loads(response.content)
    
    page_type = PageType(data.get("page_type", "unknown"))
    
    state["page_type"] = page_type
    state["messages"].append(AIMessage(content=f"页面类型: {page_type.value}"))
    _add_log(state, "INFO", f"分类结果: {page_type.value}")
    
    return state


async def plan_node(state: AgentState) -> AgentState:
    """制定爬取计划"""
    _add_log(state, "INFO", "【2/5】正在制定爬取计划...")
    llm = get_llm()
    user_input = state["user_input"]
    page_type = state.get("page_type", PageType.UNKNOWN)
    seed_urls = state.get("seed_urls", [])
    
    # 如果有 SiteAnalysis，提取目标字段
    target_fields = []
    if state.get("site_analysis"):
        sa = state["site_analysis"]
        if sa.selectors:
            target_fields = [
                sa.selectors.title, sa.selectors.url,
                *sa.selectors.extra.keys()
            ]
    
    prompt = PLAN_PROMPT.format(
        user_input=user_input,
        page_type=page_type.value,
        seed_urls=seed_urls,
        target_fields=target_fields or ["title", "url", "content"],
    )
    
    _add_log(state, "INFO", "调用 LLM 规划爬取策略...")
    response = await get_llm().ainvoke([
        {"role": "system", "content": "你是爬虫规划师，输出严格 JSON。"},
        {"role": "user", "content": prompt}
    ])
    
    import re
    json_match = re.search(r'\{.*\}', response.content, re.DOTALL)
    data = json.loads(json_match.group()) if json_match else json.loads(response.content)
    
    user_max_pages = state.get("max_pages")
    user_max_depth = state.get("max_depth")
    
    plan = CrawlPlan(
        seed_urls=data.get("seed_urls", seed_urls),
        target_schema=data.get("target_schema", data.get("fields", {})),
        max_pages=user_max_pages if user_max_pages else data.get("max_pages", 50),
        max_depth=user_max_depth if user_max_depth else data.get("max_depth", 2),
        per_domain_rate=data.get("per_domain_rate", 0.5),
        require_login=data.get("require_login", False),
        cookies=data.get("cookies"),
        user_instructions=user_input,
    )
    
    # 创建 Job
    job = CrawlJob(
        id=str(uuid.uuid4())[:8],
        plan=plan,
        site_analysis=state.get("site_analysis"),
        status=CrawlJobStatus.PENDING,
    )
    
    state["crawl_plan"] = plan
    state["current_job"] = job
    state["messages"].append(AIMessage(content=f"计划已制定: {plan.max_pages} 页"))
    _add_log(state, "INFO", f"计划制定完成: 最多 {plan.max_pages} 页，深度 {plan.max_depth} 级")
    
    return state


async def analyze_node(state: AgentState) -> AgentState:
    """SiteAnalyzer：分析站点结构"""
    _add_log(state, "INFO", "【3/5】正在分析站点结构...")
    urls = state["crawl_plan"].seed_urls
    if not urls:
        _add_log(state, "WARNING", "无种子 URL，跳过站点分析")
        return state
    
    # 分析第一个种子 URL
    url = urls[0]
    state["messages"].append(AIMessage(content=f"正在分析站点: {url}"))
    _add_log(state, "INFO", f"分析目标站点: {url}")
    
    try:
        analysis = await analyze_site(url)
        state["site_analysis"] = analysis
        
        # 更新 Job
        if state["current_job"]:
            state["current_job"].site_analysis = analysis
        
        state["messages"].append(
            AIMessage(content=f"站点分析完成: {analysis.page_type.value}, 置信度 {analysis.confidence:.1%}")
        )
        _add_log(state, "INFO", f"站点分析完成: 类型={analysis.page_type.value}, 置信度={analysis.confidence:.1%}")
        
        # 如果发现 API，切换数据源
        if analysis.api_endpoint:
            state["messages"].append(
                AIMessage(content=f"发现 API 接口: {analysis.api_endpoint}")
            )
            _add_log(state, "INFO", f"发现 API 接口: {analysis.api_endpoint}")
            
    except Exception as e:
        logger.warning(f"站点分析失败: {e}")
        state["messages"].append(AIMessage(content=f"站点分析失败，使用通用模式: {e}"))
        _add_log(state, "WARNING", f"站点分析失败，使用通用提取模式: {e}")
    
    return state


async def execute_node(state: AgentState) -> AgentState:
    """执行爬取"""
    _add_log(state, "INFO", "【4/5】开始执行爬取任务...")
    job = state["current_job"]
    if not job:
        state["error_message"] = "无任务可执行"
        _add_log(state, "ERROR", "无任务可执行")
        return state
    
    job.status = CrawlJobStatus.RUNNING
    job.stats["start_time"] = time.time()
    
    # 初始化组件
    _add_log(state, "INFO", "初始化爬虫组件...")
    frontier = SQLiteFrontier()
    await frontier.initialize()
    
    fetcher = Fetcher(
        frontier=frontier,
        per_domain_rate=job.plan.per_domain_rate,
    )
    
    # 添加种子 URL
    seed_count = len(job.plan.seed_urls)
    await frontier.add_urls_batch([
        {"url": u, "depth": 0, "priority": 10}
        for u in job.plan.seed_urls
    ])
    _add_log(state, "INFO", f"已添加 {seed_count} 个种子 URL 到队列")
    
    # 准备提取 Schema
    target_schema = job.plan.target_schema or {}
    if "fields" in target_schema and isinstance(target_schema["fields"], dict):
        target_schema = target_schema["fields"]
    DynamicItem = create_dynamic_schema(target_schema, "CrawlItem")
    
    # Selectors
    selectors = state["site_analysis"].selectors if state.get("site_analysis") else None
    if selectors and selectors.item:
        _add_log(state, "INFO", f"使用 CSS 选择器提取: {selectors.item}")
    else:
        _add_log(state, "INFO", "使用通用提取模式")
    
    all_items = []
    page_count = 0
    
    try:
        async with fetcher:
            _add_log(state, "INFO", "开始抓取页面...")
            while page_count < job.plan.max_pages:
                # 获取下一个 URL
                records = await frontier.pop_next(limit=5)
                if not records:
                    _add_log(state, "INFO", "队列已空，爬取完成")
                    break
                
                urls = [r.url for r in records]
                
                # 批量抓取
                results = await fetcher.fetch_batch(urls)
                
                for rec, result in zip(records, results):
                    if isinstance(result, Exception):
                        await frontier.mark_done(rec.url, False, str(result))
                        job.stats["errors"] += 1
                        continue
                    
                    if result.success:
                        # 提取数据
                        if selectors and selectors.item:
                            items = DEFAULT_EXTRACTOR.extract(
                                url=result.url,
                                html=result.html,
                                selectors=selectors,
                                target_schema=DynamicItem,
                                base_url=result.url,
                            )
                        else:
                            # 通用提取
                            items = DEFAULT_EXTRACTOR._fallback_generic(result, result.url, DynamicItem)
                        
                        all_items.extend(items)
                        job.stats["items_extracted"] += len(items)
                        
                        # 发现新链接
                        new_link_count = 0
                        if result.links:
                            new_urls = [
                                {"url": url, "depth": rec.depth + 1, "parent_url": rec.url}
                                for url, _ in result.links
                                if rec.depth + 1 <= job.plan.max_depth
                            ]
                            if new_urls:
                                added = await frontier.add_urls_batch(new_urls)
                                new_link_count = len(added) if hasattr(added, '__len__') else len(new_urls)
                        
                        await frontier.mark_done(result.url, True)
                        page_count += 1
                        job.stats["pages_crawled"] = page_count
                        
                        # 每 5 页输出一次进度
                        if page_count % 5 == 0 or page_count == 1:
                            _add_log(state, "INFO", 
                                f"进度: {page_count}/{job.plan.max_pages} 页，已提取 {len(all_items)} 条数据"
                                + (f"，新增 {new_link_count} 个链接" if new_link_count > 0 else ""))
                    else:
                        await frontier.mark_done(result.url, False, result.error)
                        job.stats["errors"] += 1
                    
                    if page_count >= job.plan.max_pages:
                        _add_log(state, "INFO", f"已达到最大页数限制 {job.plan.max_pages}")
                        break
                        
    except Exception as e:
        logger.error(f"爬取执行错误: {e}")
        state["error_message"] = str(e)
        _add_log(state, "ERROR", f"爬取出错: {e}")
    
    finally:
        await frontier.close()
        job.stats["end_time"] = time.time()
        job.stats["duration"] = job.stats["end_time"] - job.stats.get("start_time", time.time())
    
    # 保存结果
    state["extracted_items"] = all_items
    state["crawl_results"] = []  # 简化
    job.stats["total_items"] = len(all_items)
    
    state["messages"].append(
        AIMessage(content=f"爬取完成: {page_count} 页, {len(all_items)} 条数据")
    )
    _add_log(state, "INFO", f"爬取完成: {page_count} 页，{len(all_items)} 条数据")
    
    return state


async def decide_node(state: AgentState) -> AgentState:
    """决策节点：处理反爬、翻页、异常"""
    _add_log(state, "INFO", "【5/5】任务收尾中...")
    job = state["current_job"]
    if not job:
        state["is_complete"] = True
        return state
    
    # 如果有错误，判断是否重试
    if state.get("error_message"):
        # 简化：直接标记完成
        job.status = CrawlJobStatus.FAILED
        state["is_complete"] = True
        _add_log(state, "ERROR", f"任务失败: {state['error_message']}")
        return state
    
    # 正常完成
    job.status = CrawlJobStatus.DONE
    state["is_complete"] = True
    state["next_action"] = "done"
    _add_log(state, "SUCCESS", "任务全部完成！")
    
    return state


# ==================== 构建主图 ====================

def build_agent_workflow():
    """构建主 Agent 工作流"""
    
    graph = StateGraph(AgentState)
    
    # 节点
    graph.add_node("classify", classify_node)
    graph.add_node("plan", plan_node)
    graph.add_node("analyze", analyze_node)
    graph.add_node("execute", execute_node)
    graph.add_node("decide", decide_node)
    
    # 边
    graph.set_entry_point("classify")
    graph.add_edge("classify", "plan")
    graph.add_edge("plan", "analyze")
    graph.add_edge("analyze", "execute")
    graph.add_edge("execute", "decide")
    
    # 条件边：decide 后可能继续或结束
    graph.add_conditional_edges(
        "decide",
        lambda s: "continue" if not s.get("is_complete") else "done",
        {"continue": "execute", "done": END}
    )
    
    # Checkpointer
    checkpointer = MemorySaver()
    
    return graph.compile(checkpointer=checkpointer)


# ==================== 运行入口 ====================

class AgentRunner:
    """Agent 运行器"""
    
    def __init__(self):
        self.app = build_agent_workflow()
    
    async def run(
        self,
        user_input: str,
        seed_urls: List[str] = None,
        thread_id: str = None,
        max_pages: int = None,
        max_depth: int = None,
    ) -> Dict[str, Any]:
        """运行 Agent"""
        if thread_id is None:
            thread_id = str(uuid.uuid4())[:8]
        
        initial_state: AgentState = {
            "user_input": user_input,
            "seed_urls": seed_urls or [],
            "thread_id": thread_id,
            "max_pages": max_pages or 50,
            "max_depth": max_depth or 2,
            "page_type": PageType.UNKNOWN.value if isinstance(PageType.UNKNOWN, PageType) else "unknown",
            "crawl_plan": None,
            "site_analysis": None,
            "current_job": None,
            "extracted_items": [],
            "crawl_results": [],
            "next_action": "",
            "decision_reason": "",
            "error_message": None,
            "messages": [HumanMessage(content=user_input)],
            "logs": [],
            "requires_human": False,
            "is_complete": False,
        }
        
        config = {"configurable": {"thread_id": thread_id}}
        
        final_state = await self.app.ainvoke(initial_state, config)
        
        return {
            "thread_id": thread_id,
            "job": final_state.get("current_job"),
            "items": final_state.get("extracted_items", []),
            "messages": [m.content for m in final_state.get("messages", [])],
            "logs": final_state.get("logs", []),
            "error": final_state.get("error_message"),
        }
    
    async def astream_state(
        self,
        user_input: str,
        seed_urls: List[str] = None,
        thread_id: str = None,
        max_pages: int = None,
        max_depth: int = None,
    ):
        """流式运行，每次状态变更都 yield 出完整 state"""
        if thread_id is None:
            thread_id = str(uuid.uuid4())[:8]
        
        initial_state: AgentState = {
            "user_input": user_input,
            "seed_urls": seed_urls or [],
            "thread_id": thread_id,
            "max_pages": max_pages or 50,
            "max_depth": max_depth or 2,
            "page_type": PageType.UNKNOWN.value if isinstance(PageType.UNKNOWN, PageType) else "unknown",
            "crawl_plan": None,
            "site_analysis": None,
            "current_job": None,
            "extracted_items": [],
            "crawl_results": [],
            "next_action": "",
            "decision_reason": "",
            "error_message": None,
            "messages": [HumanMessage(content=user_input)],
            "logs": [],
            "requires_human": False,
            "is_complete": False,
        }
        
        config = {"configurable": {"thread_id": thread_id}}
        
        async for state in self.app.astream(initial_state, config):
            yield state
    
    async def run_simple(
        self,
        url: str,
        fields: List[str] = None,
        max_pages: int = 10,
    ) -> List[Dict]:
        """简化运行：直接给 URL 和字段"""
        user_input = f"爬取 {url} 的 {'、'.join(fields or ['标题、链接、内容'])}"
        result = await self.run(user_input, seed_urls=[url])
        
        items = result.get("items", [])
        return [item.model_dump() if hasattr(item, "model_dump") else item for item in items]


# 单例
_runner = None

def get_agent_runner() -> AgentRunner:
    global _runner
    if _runner is None:
        _runner = AgentRunner()
    return _runner


# ==================== 导出 ====================

__all__ = [
    "AgentState",
    "AgentRunner",
    "build_agent_workflow",
    "get_agent_runner",
]