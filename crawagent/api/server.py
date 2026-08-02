from __future__ import annotations

import asyncio
import json
import os
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from crawagent.config.settings import get_settings
from crawagent.core.fetcher import Fetcher
from crawagent.core.frontier import SQLiteFrontier
from crawagent.core.extractor import DEFAULT_EXTRACTOR
from crawagent.core.job_store import get_job_store
from crawagent.graph.site_analyzer import analyze_site
from crawagent.graph.anti_bot import handle_anti_bot_challenge
from crawagent.core.models import (
    SiteAnalysis, Selectors, CrawlPlan, CrawlJob, PageType, CrawlJobStatus
)
from crawagent.harness import (
    CrawlHarness, SessionManager, CrawlHooks, create_default_tools,
    create_antibot_hooks, CrawlEnv, RunResult,
)
from crawagent.security.security_hook import create_security_hooks
from crawagent.core.database import get_db, close_db
from loguru import logger


# ==================== Harness 实例 ====================

_harness: Optional[CrawlHarness] = None
_db_instance = None


async def _get_harness() -> CrawlHarness:
    """获取 CrawlHarness 单例"""
    global _harness
    if _harness is None:
        settings = get_settings()
        session_manager = SessionManager(settings.mysql_dsn)
        await session_manager.initialize()
        hooks = CrawlHooks()
        # 注册 AntiBot hooks（检测反爬 → 自动注入升级策略）
        create_antibot_hooks(hooks)
        # 注册 Security hooks（拦截危险工具调用）
        create_security_hooks(hooks)
        tool_registry = create_default_tools()
        _harness = CrawlHarness(
            session_manager=session_manager,
            hooks=hooks,
            tools=tool_registry.list_tools(),
            tool_executors=tool_registry.get_all_executors(),
        )
    return _harness


# ==================== 请求/响应模型 ====================

class RunRequest(BaseModel):
    instruction: str
    seed_urls: List[str] = []
    thread_id: Optional[str] = None
    max_pages: int = 50
    max_depth: int = 2


class ResumeRequest(BaseModel):
    """断点续抓请求（P1-4）"""
    job_id: str = ""         # 任务 ID（URL metadata 含 job_id 时按任务过滤；空则全部）
    max_retries: int = 3     # 失败重试上限


class AnalyzeRequest(BaseModel):
    url: str


class CrawlDirectRequest(BaseModel):
    urls: List[str]
    selectors: Optional[Dict[str, str]] = None
    max_pages: int = 50


class JobStatusResponse(BaseModel):
    job_id: str
    status: str
    progress: Dict[str, int]
    items_count: int
    items: List[Dict[str, Any]] = []
    messages: List[str] = []
    error: Optional[str] = None
    logs: List[Dict[str, str]] = []
    created_at: Optional[float] = None
    updated_at: Optional[float] = None


class RunResponse(BaseModel):
    thread_id: str
    job_id: str
    status: str = "running"


# ---- Harness 请求/响应模型 ----

class HarnessPromptRequest(BaseModel):
    """CrawlHarness prompt 请求"""
    message: str
    session_id: Optional[str] = None
    lane: str = "main"
    max_pages: Optional[int] = None
    max_depth: Optional[int] = None


class HarnessPromptResponse(BaseModel):
    """CrawlHarness prompt 响应"""
    session_id: str
    kind: str  # "completed" / "needs_input" / "error" / "cancelled"
    data: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    usage: Optional[Dict[str, Any]] = None      # 本次 prompt 的 token 用量
    usage_total: Optional[Dict[str, Any]] = None  # 该 session 累计 token 用量


class HarnessQuickCrawlRequest(BaseModel):
    """快速爬取请求"""
    url: str
    instruction: str = ""


class HarnessSessionResponse(BaseModel):
    """Session 响应"""
    session_id: str
    name: str
    lanes: List[Dict[str, Any]] = []


# 内存缓存（运行中的任务用，结束后持久化）
_job_cache: Dict[str, Dict] = {}


async def _get_job(job_id: str) -> Optional[Dict]:
    """优先从缓存取，没有从数据库取"""
    if job_id in _job_cache:
        return _job_cache[job_id]
    # 从数据库加载
    store = get_job_store()
    job = await store.get_job(job_id)
    if job:
        _job_cache[job_id] = job
    return job


async def _save_job_to_db(job_id: str, final: bool = False):
    """把缓存中的任务同步到数据库"""
    if job_id not in _job_cache:
        return
    job = _job_cache[job_id]
    store = get_job_store()
    
    # 检查是否存在
    existing = await store.get_job(job_id)
    if not existing:
        await store.create_job(job_id, job.get("instruction", ""), job.get("seed_urls", []))
    
    update_data = {
        "status": job.get("status", "running"),
        "progress": job.get("progress", {}),
        "items_count": job.get("items_count", 0),
        "items": job.get("items", []),
        "logs": job.get("logs", []),
        "error": job.get("error"),
    }
    await store.update_job(job_id, **update_data)
    # 注意：完成后不删除缓存。_get_job 优先读缓存，数据库表不含
    # messages/session_id 等字段，删缓存会导致这些数据在状态查询时丢失。


async def _run_agent_background(job_id: str, instruction: str, seed_urls: List[str], thread_id: str, max_pages: int = 50, max_depth: int = 2):
    """旧接口兼容转发：内部走 CrawlHarness（双轨合一的唯一执行路径）

    保留 /api/agent/run 的异步任务 + 状态轮询语义，实际执行完全由 harness Loop 承担。
    """
    try:
        # 初始化缓存
        _job_cache[job_id] = {
            "job_id": job_id,
            "instruction": instruction,
            "seed_urls": seed_urls,
            "status": "running",
            "progress": {"pages_crawled": 0, "pages_total": 1},
            "items_count": 0,
            "items": [],
            "messages": [],
            "error": None,
            "logs": [{"level": "INFO", "message": f"任务 {job_id} 开始执行"}],
        }

        # 先写入数据库
        await _save_job_to_db(job_id)

        _job_cache[job_id]["logs"].append({"level": "INFO", "message": "初始化 Harness..."})

        harness = await _get_harness()

        # thread_id 兼容：复用为 harness session；不存在则创建
        session = await harness.get_session(thread_id) if thread_id else None
        if session is None:
            session = await harness.create_session(name=f"agent:{instruction[:30]}")
        session_id = session.session_id
        _job_cache[job_id]["session_id"] = session_id

        _job_cache[job_id]["logs"].append({"level": "INFO", "message": "开始执行 Agent Loop..."})

        # 用户参数以任务备注注入（与 /api/harness/prompt 一致），覆盖 LLM 默认规划
        task_notes = ""
        if max_pages is not None or max_depth is not None:
            parts = ["本次爬取任务的资源预算（严格遵守）："]
            if max_pages is not None:
                parts.append(f"- 最多抓取 {max_pages} 个页面（不要超过）")
            if max_depth is not None:
                parts.append(f"- 抓取深度最多 {max_depth} 层")
            task_notes = "\n".join(parts)

        # 组装消息：指令 + 种子 URL
        message = instruction
        if seed_urls:
            message += "\n\n目标URL:\n" + "\n".join(seed_urls)

        try:
            result = await asyncio.wait_for(
                harness.prompt(session_id, message, task_notes=task_notes),
                timeout=300,
            )
        except asyncio.TimeoutError:
            raise RuntimeError("任务执行超时（5分钟）")

        # 从会话消息树提取最终输出（items / messages）
        final_items: List[Any] = []
        final_messages: List[str] = []
        final_error = result.error
        try:
            entries = await session.get_entries(limit=1000, order="asc")
        except Exception as _e:
            logger.warning(f"任务 {job_id} 读取 session entries 失败: {_e}")
            entries = []
        for e in entries:
            if e.role == "tool" and e.content:
                # extract/save/deep_crawl 等工具的返回 JSON 中含 items / pages 数组
                try:
                    payload = json.loads(e.content)
                    items = payload.get("items") if isinstance(payload, dict) else None
                    if not isinstance(items, list):
                        items = payload.get("pages") if isinstance(payload, dict) else None
                    if isinstance(items, list):
                        final_items.extend(items)
                except Exception:
                    pass
            elif e.content and not (e.tool_calls or e.tool_call_id):
                final_messages.append(e.content)

        if not final_error and result.kind != "completed":
            final_error = final_error or f"任务状态: {result.kind}"

        items_dict = [
            i if isinstance(i, dict) else getattr(i, "model_dump", lambda: i)()
            for i in final_items
        ]

        _job_cache[job_id].update({
            "status": "failed" if final_error else "completed",
            "progress": {"pages_crawled": len(items_dict), "pages_total": len(items_dict)},
            "items_count": len(items_dict),
            "items": items_dict,
            "messages": final_messages,
            "error": final_error,
            "logs": _job_cache[job_id]["logs"] + (
                [{"level": "SUCCESS", "message": "任务完成"}] if not final_error
                else [{"level": "ERROR", "message": final_error}]
            ),
        })

        # 最终写入数据库
        await _save_job_to_db(job_id, final=True)
    except Exception as e:
        logger.exception(f"任务 {job_id} 执行失败: {e}")
        if job_id in _job_cache:
            _job_cache[job_id].update({
                "status": "failed",
                "error": str(e),
                "logs": _job_cache[job_id]["logs"] + [{"level": "ERROR", "message": str(e)}],
            })
            await _save_job_to_db(job_id, final=True)


# ==================== 生命周期 ====================

@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动
    settings = get_settings()
    logger.info(f"启动 {settings.app_name} v{settings.app_version}")
    
    # 禁用系统代理（防止 Privoxy 等代理拦截请求）
    import os
    os.environ["no_proxy"] = "*"
    os.environ["NO_PROXY"] = "*"
    
    # 初始化数据库
    global _db_instance
    _db_instance = await get_db()
    logger.info("数据库初始化完成")
    
    # 预热旧组件（兼容）
    frontier = SQLiteFrontier()
    await frontier.initialize()
    await frontier.close()
    
    yield
    
    # 关闭
    global _harness
    if _harness:
        await _harness.close()
        _harness = None
    await close_db()
    logger.info("关闭服务")


app = FastAPI(
    title="CrawAgent API",
    description="基于 LLM Agent 的智能爬虫系统",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS 中间件
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ==================== API 路由 ====================

@app.get("/api/health")
async def health():
    from crawagent.config.settings import get_settings
    s = get_settings()
    db_ok = False
    try:
        if _db_instance is not None:
            from sqlalchemy import text
            async with _db_instance.engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
                db_ok = True
    except Exception:
        db_ok = False
    return {
        "status": "ok",
        "service": "CrawAgent",
        "database": db_ok,
        "mock_mode": s.mock_mode,
    }


@app.post("/api/agent/run", response_model=RunResponse)
async def run_agent(req: RunRequest):
    """运行 Agent 任务（异步）"""
    job_id = str(uuid.uuid4())[:8]
    thread_id = req.thread_id or str(uuid.uuid4())
    
    asyncio.create_task(
        _run_agent_background(job_id, req.instruction, req.seed_urls, thread_id, req.max_pages, req.max_depth)
    )
    
    return RunResponse(thread_id=thread_id, job_id=job_id, status="running")


@app.get("/api/agent/status/{job_id}", response_model=JobStatusResponse)
async def get_job_status(job_id: str):
    """查询任务状态"""
    job = await _get_job(job_id)
    if not job:
        raise HTTPException(404, f"任务 {job_id} 不存在")
    return job


@app.post("/api/agent/resume")
async def resume_job(req: ResumeRequest):
    """断点续抓（P1-4）：将 frontier 中失败/中断的 URL 重新入队

    deep_crawl 抓取失败的页面会写入 frontier 重试队列，
    调用此端点后可从上次断点继续抓取。
    """
    frontier = SQLiteFrontier()
    await frontier.initialize()
    try:
        restored = await frontier.resume_job(max_retries=req.max_retries, job_id=req.job_id)
        snapshot = await frontier.snapshot_job(req.job_id)
    except Exception as e:
        logger.error(f"断点续抓失败: {e}")
        raise HTTPException(500, f"断点续抓失败: {e}")
    finally:
        await frontier.close()
    return {
        "restored": restored,
        "snapshot": snapshot,
        "job_id": req.job_id or "（全部）",
    }


@app.get("/api/agent/jobs")
async def list_jobs(limit: int = 20, status: str = None):
    """获取任务列表"""
    store = get_job_store()
    jobs = await store.list_jobs(limit=limit, status=status)
    return {
        "total": len(jobs),
        "jobs": jobs,
    }


@app.delete("/api/agent/jobs/{job_id}")
async def delete_job(job_id: str):
    """删除任务"""
    store = get_job_store()
    ok = await store.delete_job(job_id)
    if not ok:
        raise HTTPException(404, f"任务 {job_id} 不存在")
    return {"ok": True}


@app.post("/api/agent/analyze", response_model=SiteAnalysis)
async def analyze_site_endpoint(req: AnalyzeRequest):
    """分析站点结构"""
    try:
        analysis = await analyze_site(req.url)
        return analysis
    except Exception as e:
        logger.error(f"站点分析失败: {e}")
        raise HTTPException(500, f"分析失败: {e}")


@app.post("/api/crawl/direct")
async def crawl_direct(req: CrawlDirectRequest):
    """直接爬取（不经过 Agent 规划）"""
    frontier = SQLiteFrontier()
    await frontier.initialize()
    
    fetcher = Fetcher(frontier=frontier, per_domain_rate=0.5)
    extractor = DEFAULT_EXTRACTOR
    
    all_items = []
    page_count = 0
    
    try:
        await frontier.add_urls_batch([
            {"url": u, "depth": 0, "priority": 10} for u in req.urls
        ])
        
        if req.selectors:
            sel_obj = Selectors(**req.selectors)
        else:
            sel_obj = None
        
        async with fetcher:
            while page_count < req.max_pages:
                records = await frontier.pop_next(limit=5)
                if not records:
                    break
                
                results = await fetcher.fetch_batch([r.url for r in records])
                
                for rec, result in zip(records, results):
                    if isinstance(result, Exception) or not result.success:
                        continue
                    
                    if sel_obj:
                        items = extractor.extract(
                            url=result.url,
                            html=result.html,
                            selectors=sel_obj,
                            target_schema=None,
                            base_url=result.url,
                        )
                    else:
                        # 无选择器时只保存基础元信息（_fallback_generic 存在于 extractor.py，
                        # 但此路径直接走轻量元信息更高效）
                        items = [{"url": result.url, "title": result.title or "", "status": result.status_code}]

                    all_items.extend([item.model_dump() if hasattr(item, "model_dump") else item for item in items])
                    
                    if result.links:
                        await frontier.add_urls_batch([
                            {"url": link_url, "depth": rec.depth + 1, "parent_url": rec.url}
                            for link_url, _ in result.links[:20]
                        ])
                    
                    await frontier.mark_done(result.url, True)
                    page_count += 1
                    
                    if page_count >= req.max_pages:
                        break
    finally:
        await fetcher.close()
        await frontier.close()
    
    return {
        "items": all_items,
        "count": len(all_items),
        "pages": page_count,
    }


@app.get("/api/jobs/{job_id}", response_model=JobStatusResponse)
async def get_job_status(job_id: str):
    """获取任务状态"""
    frontier = SQLiteFrontier()
    await frontier.initialize()
    
    try:
        job = await frontier.get_job(job_id)
        if not job:
            raise HTTPException(404, "任务不存在")
        
        items = await frontier.get_items(job_id, limit=1)
        return JobStatusResponse(
            job_id=job[0],
            status=job[3],
            progress=json.loads(job[4]),
            items_count=len(items),
        )
    finally:
        await frontier.close()


@app.get("/api/jobs/{job_id}/items")
async def get_job_items(job_id: str, limit: int = 100, offset: int = 0):
    """获取任务提取结果"""
    frontier = SQLiteFrontier()
    await frontier.initialize()
    
    try:
        items = await frontier.get_items(job_id, limit=limit, offset=offset)
        return {"items": items, "total": len(items)}
    finally:
        await frontier.close()


@app.post("/api/anti-bot/handle")
async def anti_bot_endpoint(
    challenge_type: str,
    current_strategy: str,
    retry_count: int,
    response_headers: Dict[str, str],
    response_status: int,
    error_message: str,
):
    """反爬挑战处理"""
    try:
        upgrade = await handle_anti_bot_challenge(
            challenge_type=challenge_type,
            current_strategy=current_strategy,
            retry_count=retry_count,
            max_retries=3,
            response_headers=response_headers,
            response_status=response_status,
            error_message=error_message,
        )
        return upgrade
    except Exception as e:
        raise HTTPException(500, f"反爬处理失败: {e}")


# ==================== Harness API 路由 ====================

@app.get("/api/harness/sessions")
async def list_harness_sessions(limit: int = 50):
    """列出所有 Session"""
    harness = await _get_harness()
    sessions = await harness._session_manager.list_sessions()
    return {"sessions": sessions[:limit]}


@app.post("/api/harness/sessions", response_model=HarnessSessionResponse)
async def create_harness_session(name: str = ""):
    """创建 CrawlHarness Session"""
    harness = await _get_harness()
    session = await harness.create_session(name=name)
    lanes = await harness.list_lanes(session.session_id)
    return HarnessSessionResponse(
        session_id=session.session_id,
        name=name,
        lanes=[{"name": l.name, "leaf_id": l.leaf_id, "has_open_operation": l.has_open_operation} for l in lanes],
    )


@app.post("/api/harness/prompt", response_model=HarnessPromptResponse)
async def harness_prompt(req: HarnessPromptRequest):
    """通过 CrawlHarness 执行 Agent Loop"""
    harness = await _get_harness()

    # 如果没有 session_id，自动创建
    if not req.session_id:
        session = await harness.create_session(name=f"prompt:{req.message[:30]}")
        session_id = session.session_id
    else:
        session_id = req.session_id

    # 用户指定的 max_pages/max_depth 以任务备注注入系统提示词，覆盖 LLM 默认规划
    task_notes = ""
    if req.max_pages is not None or req.max_depth is not None:
        parts = ["本次爬取任务的资源预算（严格遵守）："]
        if req.max_pages is not None:
            parts.append(f"- 最多抓取 {req.max_pages} 个页面（不要超过）")
        if req.max_depth is not None:
            parts.append(f"- 抓取深度最多 {req.max_depth} 层")
        task_notes = "\n".join(parts)

    result = await harness.prompt(
        session_id, req.message, lane_name=req.lane, task_notes=task_notes
    )

    # 提取 usage 字段（harness 已写入 result.data["usage"] / ["usage_total"]）
    usage = (result.data or {}).get("usage")
    usage_total = (result.data or {}).get("usage_total")

    return HarnessPromptResponse(
        session_id=session_id,
        kind=result.kind,
        data=result.data,
        error=result.error,
        usage=usage,
        usage_total=usage_total,
    )


@app.post("/api/harness/quick-crawl", response_model=HarnessPromptResponse)
async def harness_quick_crawl(req: HarnessQuickCrawlRequest):
    """快速爬取（自动创建 session）"""
    harness = await _get_harness()
    result = await harness.quick_crawl(url=req.url, instruction=req.instruction)

    # quick_crawl 已把 session_id 塞进 result.data
    session_id = (result.data or {}).get("session_id", "")

    return HarnessPromptResponse(
        session_id=session_id,
        kind=result.kind,
        data=result.data,
        error=result.error,
    )


@app.get("/api/harness/sessions/{session_id}/entries")
async def get_harness_entries(session_id: str, limit: int = 50):
    """获取 Session 的对话历史"""
    harness = await _get_harness()
    session = await harness._session_manager.get_session(session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    # 前端展示"最新在前"，用 desc；loop 内部调用走默认 asc
    entries = await session.get_entries(limit=limit, order="desc")
    return {
        "entries": [
            {
                "id": e.id,
                "parent_id": e.parent_id,
                "role": e.role,
                "content": e.content,
                "tool_calls": e.tool_calls,
                "tool_call_id": e.tool_call_id,
                "created_at": e.created_at,
            }
            for e in entries
        ]
    }


@app.get("/api/harness/sessions/{session_id}/usage")
async def get_harness_usage(session_id: str):
    """获取 Session 的累计 token 用量（含缓存命中率与估算费用）"""
    harness = await _get_harness()
    session = await harness._session_manager.get_session(session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    usage = harness.get_session_usage(session_id)
    from crawagent.harness.harness import _usage_to_dict
    return {"session_id": session_id, "usage": _usage_to_dict(usage)}


@app.delete("/api/harness/sessions/{session_id}")
async def delete_harness_session(session_id: str):
    """删除 Session 及其所有关联数据（对话树 / lanes / 操作日志 / 全局事实）"""
    harness = await _get_harness()
    deleted = await harness._session_manager.delete_session(session_id)
    if not deleted:
        raise HTTPException(404, "Session not found")
    # 同时清理内存缓存
    harness._sessions.pop(session_id, None)
    harness._loops.pop(session_id, None)
    return {"deleted": True, "session_id": session_id}


@app.get("/api/harness/sessions/{session_id}/lanes")
async def list_harness_lanes(session_id: str):
    """列出 Session 的 Lanes"""
    harness = await _get_harness()
    lanes = await harness.list_lanes(session_id)
    return {"lanes": [{"name": l.name, "leaf_id": l.leaf_id, "has_open_operation": l.has_open_operation} for l in lanes]}


@app.post("/api/harness/sessions/{session_id}/lanes")
async def create_harness_lane(session_id: str, name: str):
    """创建 Lane"""
    harness = await _get_harness()
    lane = await harness.create_lane(session_id, name)
    return {"name": lane.name, "leaf_id": lane.leaf_id}


@app.post("/api/harness/sessions/{session_id}/stop")
async def stop_harness_session(session_id: str):
    """停止 Session 的运行"""
    harness = await _get_harness()
    await harness.stop(session_id)
    return {"ok": True}


@app.get("/api/stats")
async def get_stats():
    """系统统计"""
    frontier = SQLiteFrontier()
    await frontier.initialize()
    
    try:
        stats = await frontier.get_stats()
        return stats
    finally:
        await frontier.close()


class SettingsResponse(BaseModel):
    openai_base_url: str
    openai_api_key: str = Field(default="", description="返回时脱敏")
    default_model: str
    default_temperature: float
    max_tokens: int
    max_concurrent: int
    per_domain_rate: float
    request_timeout: float
    max_retries: int
    max_pages: int
    max_depth: int
    impersonate: str
    use_curl_cffi: bool
    mock_mode: bool


class SettingsUpdateRequest(BaseModel):
    openai_base_url: Optional[str] = None
    openai_api_key: Optional[str] = None
    default_model: Optional[str] = None
    default_temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    max_concurrent: Optional[int] = None
    per_domain_rate: Optional[float] = None
    request_timeout: Optional[float] = None
    max_retries: Optional[int] = None
    max_pages: Optional[int] = None
    max_depth: Optional[int] = None
    impersonate: Optional[str] = None
    use_curl_cffi: Optional[bool] = None
    mock_mode: Optional[bool] = None


@app.get("/api/settings", response_model=SettingsResponse)
async def get_settings_endpoint():
    """获取当前配置"""
    s = get_settings()
    return SettingsResponse(
        openai_base_url=s.openai_base_url,
        openai_api_key="******" if s.openai_api_key else "",
        default_model=s.default_model,
        default_temperature=s.default_temperature,
        max_tokens=s.max_tokens,
        max_concurrent=s.max_concurrent,
        per_domain_rate=s.per_domain_rate,
        request_timeout=s.request_timeout,
        max_retries=s.max_retries,
        max_pages=s.max_pages,
        max_depth=s.max_depth,
        impersonate=s.impersonate,
        use_curl_cffi=s.use_curl_cffi,
        mock_mode=s.mock_mode,
    )


@app.put("/api/settings")
async def update_settings(req: SettingsUpdateRequest):
    """更新配置（写入 .env 并清除缓存）"""
    env_path = Path(".env")
    
    current_env = {}
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").split("\n"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                current_env[key] = value
    
    mapping = {
        "openai_base_url": "OPENAI_BASE_URL",
        "openai_api_key": "OPENAI_API_KEY",
        "default_model": "DEFAULT_MODEL",
        "default_temperature": "DEFAULT_TEMPERATURE",
        "max_tokens": "MAX_TOKENS",
        "max_concurrent": "MAX_CONCURRENT",
        "per_domain_rate": "PER_DOMAIN_RATE",
        "request_timeout": "REQUEST_TIMEOUT",
        "max_retries": "MAX_RETRIES",
        "max_pages": "MAX_PAGES",
        "max_depth": "MAX_DEPTH",
        "impersonate": "IMPERSONATE",
        "use_curl_cffi": "USE_CURL_CFFI",
        "mock_mode": "MOCK_MODE",
    }
    
    for field, env_key in mapping.items():
        value = getattr(req, field, None)
        if value is not None:
            current_env[env_key] = str(value)
    
    lines = []
    for key, value in current_env.items():
        lines.append(f"{key}={value}")
    
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    
    from crawagent.config.settings import get_settings
    from crawagent.llm.factory import get_factory
    
    get_settings.cache_clear()
    get_factory.cache_clear()
    # 重新加载 factory 的 providers
    factory = get_factory()
    factory.reload()
    
    return {"message": "配置已保存，立即生效"}


# ==================== Output API 路由（P3） ====================

class OutputListRequest(BaseModel):
    """输出文件列表请求"""
    subdir: str = ""
    pattern: str = "**/*"
    limit: int = 200


class OutputRenderRequest(BaseModel):
    """路径模板渲染请求（预览用）"""
    template: str
    url: str = ""
    title: str = ""
    ext: str = "md"


@app.get("/api/output/files")
async def list_output_files(subdir: str = "", pattern: str = "**/*", limit: int = 200):
    """列出已保存的输出文件"""
    from crawagent.output import FileOrganizer
    org = FileOrganizer(base_dir="./output")
    files = org.list_files(subdir=subdir, pattern=pattern, limit=limit)
    return {"total": len(files), "files": files}


@app.post("/api/output/preview-path")
async def preview_output_path(req: OutputRenderRequest):
    """预览路径模板渲染结果（不实际写入）"""
    from crawagent.output import FileOrganizer
    org = FileOrganizer(base_dir="./output")
    path = org.render(req.template, url=req.url, title=req.title, ext=req.ext)
    return {"template": req.template, "rendered_path": path}


@app.get("/api/output/file")
async def read_output_file(path: str):
    """读取已保存文件内容"""
    from pathlib import Path
    output_dir = Path("./output").resolve()
    abs_path = Path(path).resolve() if not Path(path).is_absolute() else Path(path)
    try:
        abs_path.relative_to(output_dir)
    except ValueError:
        raise HTTPException(403, "路径越权：只能读取 output 目录下的文件")
    if not abs_path.is_file():
        raise HTTPException(404, f"文件不存在: {abs_path}")
    try:
        with open(abs_path, "r", encoding="utf-8") as f:
            content = f.read()
        return {"path": str(abs_path), "content": content, "size": len(content)}
    except Exception as e:
        raise HTTPException(500, f"读取失败: {e}")


@app.delete("/api/output/file")
async def delete_output_file(path: str):
    """删除已保存文件"""
    from pathlib import Path
    output_dir = Path("./output").resolve()
    abs_path = Path(path).resolve() if not Path(path).is_absolute() else Path(path)
    try:
        abs_path.relative_to(output_dir)
    except ValueError:
        raise HTTPException(403, "路径越权：只能删除 output 目录下的文件")
    if not abs_path.is_file():
        raise HTTPException(404, f"文件不存在: {abs_path}")
    try:
        abs_path.unlink()
        return {"deleted": True, "path": str(abs_path)}
    except Exception as e:
        raise HTTPException(500, f"删除失败: {e}")


@app.post("/api/output/open")
async def open_output_path(path: str):
    """用系统默认程序打开文件或在资源管理器中打开文件夹"""
    import subprocess
    import sys
    from pathlib import Path
    output_dir = Path("./output").resolve()
    abs_path = Path(path).resolve() if not Path(path).is_absolute() else Path(path)
    try:
        abs_path.relative_to(output_dir)
    except ValueError:
        raise HTTPException(403, f"路径越权：只能打开 output 目录下的文件/文件夹。output_dir={output_dir}, abs_path={abs_path}")
    if not abs_path.exists():
        raise HTTPException(404, f"路径不存在: {abs_path}")
    try:
        if sys.platform == 'win32':
            os.startfile(str(abs_path))
        elif sys.platform == 'darwin':
            subprocess.Popen(['open', str(abs_path)])
        else:
            subprocess.Popen(['xdg-open', str(abs_path)])
        return {"opened": True, "path": str(abs_path), "is_dir": abs_path.is_dir()}
    except Exception as e:
        raise HTTPException(500, f"打开失败: {e}")


# ==================== Monitor API 路由（P4） ====================

class MonitorTaskRequest(BaseModel):
    """创建/更新监控任务请求"""
    name: str = ""
    url: str
    interval_minutes: int = 360
    watch_fields: List[str] = []
    css_selector: str = ""
    alert_webhook: str = ""
    alert_cooldown_minutes: int = 60
    enabled: bool = True


@app.get("/api/monitor/tasks")
async def list_monitor_tasks(enabled_only: bool = False):
    """列出所有监控任务"""
    from crawagent.monitor import get_monitor_store
    store = get_monitor_store()
    tasks = store.list_tasks(enabled_only=enabled_only)
    return {
        "total": len(tasks),
        "tasks": [t.model_dump() for t in tasks],
    }


@app.post("/api/monitor/tasks")
async def create_monitor_task(req: MonitorTaskRequest):
    """创建监控任务并立即触发首次检查"""
    from crawagent.monitor import MonitorTask, ScheduleType, get_monitor_store, MonitorScheduler
    task = MonitorTask(
        name=req.name or f"监控-{req.url[:30]}",
        url=req.url,
        schedule_type=ScheduleType.INTERVAL,
        interval_seconds=req.interval_minutes * 60,
        watch_fields=req.watch_fields,
        css_selector=req.css_selector,
        alert_webhook=req.alert_webhook,
        alert_cooldown=req.alert_cooldown_minutes * 60,
        enabled=req.enabled,
    )
    store = get_monitor_store()
    store.create_task(task)

    # 立即触发首次检查（建立基线）
    scheduler = MonitorScheduler(store=store)
    first_result = await scheduler.run_task_once(task.id)
    return {
        "task": task.model_dump(),
        "first_check": first_result,
    }


@app.get("/api/monitor/tasks/{task_id}")
async def get_monitor_task(task_id: str):
    """获取监控任务详情"""
    from crawagent.monitor import get_monitor_store
    store = get_monitor_store()
    task = store.get_task(task_id)
    if not task:
        raise HTTPException(404, f"监控任务不存在: {task_id}")
    # 附带基线
    baseline = store.get_baseline(task_id, task.url)
    return {
        "task": task.model_dump(),
        "baseline": baseline,
    }


@app.put("/api/monitor/tasks/{task_id}")
async def update_monitor_task(task_id: str, req: MonitorTaskRequest):
    """更新监控任务"""
    from crawagent.monitor import get_monitor_store
    store = get_monitor_store()
    task = store.get_task(task_id)
    if not task:
        raise HTTPException(404, f"监控任务不存在: {task_id}")
    task.name = req.name
    task.url = req.url
    task.interval_seconds = req.interval_minutes * 60
    task.watch_fields = req.watch_fields
    task.css_selector = req.css_selector
    task.alert_webhook = req.alert_webhook
    task.alert_cooldown = req.alert_cooldown_minutes * 60
    task.enabled = req.enabled
    store.update_task(task)
    return {"task": task.model_dump()}


@app.delete("/api/monitor/tasks/{task_id}")
async def delete_monitor_task(task_id: str):
    """删除监控任务（级联删除基线 + 告警）"""
    from crawagent.monitor import get_monitor_store
    store = get_monitor_store()
    ok = store.delete_task(task_id)
    if not ok:
        raise HTTPException(404, f"监控任务不存在: {task_id}")
    return {"deleted": True, "task_id": task_id}


@app.post("/api/monitor/tasks/{task_id}/check")
async def trigger_monitor_check(task_id: str):
    """手动触发一次监控检查"""
    from crawagent.monitor import get_monitor_store, MonitorScheduler
    store = get_monitor_store()
    scheduler = MonitorScheduler(store=store)
    result = await scheduler.run_task_once(task_id)
    return result


@app.get("/api/monitor/alerts")
async def list_monitor_alerts(task_id: str = "", limit: int = 50):
    """列出告警记录"""
    from crawagent.monitor import get_monitor_store
    store = get_monitor_store()
    alerts = store.list_alerts(task_id=task_id, limit=limit)
    return {
        "total": len(alerts),
        "alerts": [a.model_dump() for a in alerts],
    }


@app.post("/api/monitor/test-webhook")
async def test_monitor_webhook(webhook_url: str = ""):
    """测试 webhook 是否可用"""
    from crawagent.monitor import Notifier
    if not webhook_url:
        raise HTTPException(400, "缺少 webhook_url 参数")
    notifier = Notifier()
    result = await notifier.send_test(webhook_url)
    return result


# ==================== 安全扫描（P5） ====================

class ScanTaskRequest(BaseModel):
    """创建/更新扫描任务请求"""
    name: str = ""
    url: str
    categories: List[str] = []      # VulnCategory 值列表，空则扫全部
    depth: int = 1
    timeout: int = 30
    max_requests: int = 100
    generate_patches: bool = True


@app.get("/api/security/categories")
async def list_vuln_categories():
    """列出支持的漏洞扫描类别"""
    from crawagent.security import VulnCategory
    items = []
    for c in VulnCategory:
        items.append({"value": c.value, "label": c.value.replace("_", " ")})
    return {"total": len(items), "categories": items}


@app.get("/api/security/tasks")
async def list_scan_tasks():
    """列出所有扫描任务"""
    from crawagent.security import get_security_store
    store = get_security_store()
    tasks = store.list_tasks()
    return {
        "total": len(tasks),
        "tasks": [t.model_dump() for t in tasks],
    }


@app.post("/api/security/tasks")
async def create_scan_task(req: ScanTaskRequest):
    """创建扫描任务并立即执行扫描"""
    from crawagent.security import SecurityLane, VulnCategory
    # 解析 categories 字符串为枚举
    categories = []
    for c_str in req.categories:
        try:
            categories.append(VulnCategory(c_str))
        except ValueError:
            # 按名称匹配（忽略大小写）
            for member in VulnCategory:
                if member.value.lower() == c_str.lower() or member.name.lower() == c_str.lower():
                    categories.append(member)
                    break
    lane = SecurityLane()
    result = await lane.run_scan(
        url=req.url,
        name=req.name,
        categories=categories or None,
        depth=req.depth,
        timeout=req.timeout,
        max_requests=req.max_requests,
        generate_patches=req.generate_patches,
    )
    task = result["task"]
    scan_result = result["scan_result"]
    patches = result["patches"]
    return {
        "task": task.model_dump(),
        "scan_result": scan_result.model_dump(),
        "patches": [
            {
                "id": p.id,
                "title": p.title,
                "target_file": p.target_file,
                "vulnerability_ids": p.vulnerability_ids,
                "severity": p.severity.value,
                "description": p.description,
                "diff": p.diff,
            }
            for p in patches
        ],
    }


@app.get("/api/security/tasks/{task_id}")
async def get_scan_task(task_id: str):
    """获取扫描任务详情（含漏洞列表）"""
    from crawagent.security import get_security_store
    store = get_security_store()
    task = store.get_task(task_id)
    if not task:
        raise HTTPException(404, f"扫描任务不存在: {task_id}")
    vulns = store.list_vulnerabilities(task_id=task_id)
    return {
        "task": task.model_dump(),
        "vulnerabilities": [v.model_dump() for v in vulns],
    }


@app.delete("/api/security/tasks/{task_id}")
async def delete_scan_task(task_id: str):
    """删除扫描任务（级联删除漏洞记录）"""
    from crawagent.security import get_security_store
    store = get_security_store()
    ok = store.delete_task(task_id)
    if not ok:
        raise HTTPException(404, f"扫描任务不存在: {task_id}")
    return {"deleted": True, "task_id": task_id}


@app.post("/api/security/tasks/{task_id}/rescan")
async def rescan_task(task_id: str):
    """重新执行扫描任务"""
    from crawagent.security import SecurityLane, get_security_store
    store = get_security_store()
    task = store.get_task(task_id)
    if not task:
        raise HTTPException(404, f"扫描任务不存在: {task_id}")
    lane = SecurityLane(store=store)
    result = await lane.run_scan(
        url=task.url,
        name=task.name,
        categories=task.categories or None,
        depth=task.depth,
        headers=task.headers,
        timeout=task.timeout,
        generate_patches=True,
    )
    scan_result = result["scan_result"]
    patches = result["patches"]
    return {
        "task": result["task"].model_dump(),
        "scan_result": scan_result.model_dump(),
        "patches": [
            {
                "id": p.id,
                "title": p.title,
                "target_file": p.target_file,
                "vulnerability_ids": p.vulnerability_ids,
                "severity": p.severity.value,
                "description": p.description,
                "diff": p.diff,
            }
            for p in patches
        ],
    }


@app.get("/api/security/tasks/{task_id}/vulnerabilities")
async def list_task_vulnerabilities(task_id: str, severity: str = ""):
    """列出某扫描任务的漏洞"""
    from crawagent.security import get_security_store
    store = get_security_store()
    task = store.get_task(task_id)
    if not task:
        raise HTTPException(404, f"扫描任务不存在: {task_id}")
    vulns = store.list_vulnerabilities(task_id=task_id, severity=severity)
    return {
        "total": len(vulns),
        "vulnerabilities": [v.model_dump() for v in vulns],
    }


@app.get("/api/security/vulnerabilities")
async def list_all_vulnerabilities(task_id: str = "", severity: str = "", limit: int = 200):
    """列出所有漏洞（可按任务/严重性过滤）"""
    from crawagent.security import get_security_store
    store = get_security_store()
    vulns = store.list_vulnerabilities(task_id=task_id, severity=severity, limit=limit)
    return {
        "total": len(vulns),
        "vulnerabilities": [v.model_dump() for v in vulns],
    }


@app.post("/api/security/tasks/{task_id}/patches")
async def generate_patches(task_id: str, output_dir: str = ""):
    """为扫描任务生成并保存修复补丁"""
    from crawagent.security import AutoFixer, get_security_store
    store = get_security_store()
    task = store.get_task(task_id)
    if not task:
        raise HTTPException(404, f"扫描任务不存在: {task_id}")
    vulns = store.list_vulnerabilities(task_id=task_id)
    if not vulns:
        raise HTTPException(400, "该任务无漏洞，无需生成补丁")
    fixer = AutoFixer()
    patches, reported_only = await fixer.generate_patches(vulns, scan_task_id=task_id)
    if not output_dir:
        output_dir = f"./output/security_patches/{task_id}"
    patch_files = await fixer.save_patches(patches, output_dir=output_dir)
    return {
        "task_id": task_id,
        "patches_generated": len(patches),
        "reported_only_count": len(reported_only),
        "reported_only": [v.model_dump() for v in reported_only],
        "patch_files": patch_files,
        "patches": [
            {
                "id": p.id,
                "title": p.title,
                "target_file": p.target_file,
                "vulnerability_ids": p.vulnerability_ids,
                "severity": p.severity.value,
                "description": p.description,
                "diff": p.diff,
            }
            for p in patches
        ],
    }


# ==================== 登录态 Profile API（P8） ====================
# 抖音等需要登录才能完整抓取的站点：
#   1. POST /api/auth/login         —— 弹出真实浏览器，用户手动登录后关闭即保存 cookie
#   2. POST /api/auth/profile/fetch —— 带登录态抓取页面
#   3. POST /api/auth/profile/export-cookies —— 导出 cookies.txt 供 yt-dlp 下载

class AuthLoginRequest(BaseModel):
    url: str = "https://www.douyin.com"
    timeout: float = 120.0           # 等待用户登录的秒数
    wait_for_navigation: str = ""    # 登录成功后的跳转 URL 片段（可选）


class AuthFetchRequest(BaseModel):
    url: str
    headless: bool = True
    wait_for: str = "domcontentloaded"
    timeout: float = 30.0


class AuthExportRequest(BaseModel):
    url: str


class AuthAnonymousRequest(BaseModel):
    url: str = "https://www.douyin.com"
    wait_ms: int = 8000
    scroll_rounds: int = 0


@app.get("/api/auth/profiles")
async def list_auth_profiles():
    """列出所有已保存登录态的站点"""
    from crawagent.sessions.profile_manager import ProfileManager
    pm = ProfileManager()
    return {"total": len(pm.list_profiles()), "profiles": pm.list_profiles()}


@app.get("/api/auth/profile")
async def get_auth_profile(url: str):
    """查询某域名是否已有登录态"""
    from crawagent.sessions.profile_manager import ProfileManager
    pm = ProfileManager()
    info = pm.get_profile_info(url)
    if not info:
        return {"has_profile": False, "domain": pm._domain_key(url), "info": None}
    return {"has_profile": pm.has_profile(url), "domain": pm._domain_key(url), "info": info}


@app.post("/api/auth/login")
async def auth_login(req: AuthLoginRequest):
    """交互式登录：打开真实浏览器让用户手动登录

    注意：此接口会阻塞直到用户完成登录并关闭浏览器窗口
    （或达到 timeout 秒），请保持请求连接。
    登录态自动保存到 ~/.crawagent/profiles/<域名>/。
    """
    from crawagent.sessions.profile_manager import ProfileManager
    pm = ProfileManager()
    try:
        result = await pm.login_interactive(
            req.url,
            wait_for_navigation=req.wait_for_navigation,
            timeout=req.timeout,
        )
        return result
    except Exception as e:
        logger.error(f"交互式登录失败: {e}")
        raise HTTPException(500, f"交互式登录失败: {e}")


@app.post("/api/auth/profile/fetch")
async def auth_profile_fetch(req: AuthFetchRequest):
    """使用已保存的登录态抓取页面（自动携带 cookie）"""
    from crawagent.sessions.profile_manager import ProfileManager
    pm = ProfileManager()
    if not pm.has_profile(req.url):
        raise HTTPException(409, f"域名 {pm._domain_key(req.url)} 无登录态，请先调用 POST /api/auth/login")
    try:
        result = await pm.fetch_with_profile(
            req.url,
            headless=req.headless,
            wait_for=req.wait_for,
            timeout=req.timeout,
        )
        return result
    except Exception as e:
        logger.error(f"带登录态抓取失败: {e}")
        raise HTTPException(500, f"带登录态抓取失败: {e}")


@app.post("/api/auth/profile/export-cookies")
async def auth_export_cookies(req: AuthExportRequest):
    """导出 Netscape cookies.txt（供 yt-dlp /api/video/download 使用）"""
    from crawagent.sessions.profile_manager import ProfileManager
    pm = ProfileManager()
    if not pm.has_profile(req.url):
        raise HTTPException(409, f"域名 {pm._domain_key(req.url)} 无登录态，请先调用 POST /api/auth/login")
    try:
        cookies_file = pm.export_cookies_txt(req.url)
        return {"success": True, "cookies_file": cookies_file}
    except Exception as e:
        raise HTTPException(500, f"导出 cookies 失败: {e}")


@app.post("/api/auth/profile/anonymous")
async def auth_anonymous_cookies(req: AuthAnonymousRequest):
    """自动获取匿名 cookie（无需登录）

    抖音等风控站点公开视频无需账号登录，只需浏览器渲染一次
    收集 ttwid 等匿名 cookie，即可让 yt-dlp 下载完整清晰视频。
    与 /api/video/download 联用可全自动下载。
    """
    from crawagent.sessions.profile_manager import ProfileManager
    pm = ProfileManager()
    try:
        result = await pm.grab_anonymous_cookies(
            req.url, wait_ms=req.wait_ms, scroll_rounds=req.scroll_rounds
        )
        return result
    except Exception as e:
        logger.error(f"匿名 cookie 获取失败: {e}")
        raise HTTPException(500, f"匿名 cookie 获取失败: {e}")


@app.delete("/api/auth/profile")
async def auth_delete_profile(url: str):
    """删除某域名的登录态"""
    from crawagent.sessions.profile_manager import ProfileManager
    pm = ProfileManager()
    ok = pm.delete_profile(url)
    return {"deleted": ok, "domain": pm._domain_key(url)}


# ==================== Video 路由（P7） ====================

class VideoDownloadRequest(BaseModel):
    url: str
    title: str = ""
    cookies_file: str = ""
    max_items: int = 0  # 播放列表最大下载数（0=全部）

class VideoInfoRequest(BaseModel):
    url: str
    cookies_file: str = ""

class VideoExtractRequest(BaseModel):
    html: str
    base_url: str = ""

class AdRemoveRequest(BaseModel):
    html: str
    custom_selectors: List[str] = []


@app.post("/api/video/download")
async def download_video(req: VideoDownloadRequest):
    """下载视频（支持播放列表）"""
    from crawagent.output.media_downloader import MediaDownloader

    dl = MediaDownloader(base_dir="./output/videos")
    try:
        # 判断是否是播放列表
        info = await dl.extract_info(req.url, req.cookies_file)
        if info.get("is_playlist"):
            result = await dl.download_playlist(
                req.url, cookies_file=req.cookies_file, max_items=req.max_items
            )
        else:
            result = await dl.download(
                req.url, title=req.title, cookies_file=req.cookies_file
            )
            result = {"success": result["success"], "total": 1,
                       "downloaded": 1 if result["success"] else 0,
                       "failed": 0 if result["success"] else 1,
                       "items": [result], "error": result.get("error", "")}
        return result
    except Exception as e:
        return {"success": False, "error": str(e), "downloaded": 0, "failed": 1}


@app.post("/api/video/info")
async def get_video_info(req: VideoInfoRequest):
    """提取视频信息（不下载）"""
    from crawagent.output.media_downloader import MediaDownloader

    dl = MediaDownloader(base_dir="./output/videos")
    result = await dl.extract_info(req.url, req.cookies_file)
    return result


@app.post("/api/video/extract")
async def extract_video_urls(req: VideoExtractRequest):
    """从 HTML 中提取视频 URL"""
    from crawagent.extractors import VideoExtractor

    extractor = VideoExtractor()
    result = extractor.extract(req.html, base_url=req.base_url)

    return {
        "success": result.has_videos,
        "videos": [
            {"url": v.url, "type": v.video_type, "source": v.source_tag, "poster": v.poster}
            for v in result.videos
        ],
        "iframes": [
            {"url": v.url, "platform": v.title}
            for v in result.iframes
        ],
        "m3u8_urls": result.m3u8_urls,
        "mp4_urls": result.raw_mp4_urls,
        "total_count": result.total_count,
    }


@app.post("/api/video/ad-remove")
async def remove_ads(req: AdRemoveRequest):
    """移除 HTML 中的广告"""
    from crawagent.extractors import AdRemover

    remover = AdRemover(custom_selectors=req.custom_selectors or None)
    cleaned = remover.remove(req.html)
    return {"success": True, "cleaned_html": cleaned}


@app.get("/api/video/files")
async def list_video_files():
    """列出已下载的视频文件"""
    import os
    video_dir = os.path.join(os.getcwd(), "output", "videos")
    if not os.path.isdir(video_dir):
        return {"files": []}

    files = []
    for name in os.listdir(video_dir):
        filepath = os.path.join(video_dir, name)
        if os.path.isfile(filepath):
            stat = os.stat(filepath)
            ext = os.path.splitext(name)[1].lower()
            if ext in (".mp4", ".webm", ".mkv", ".flv", ".avi", ".m4a", ".mp3", ".m4v"):
                files.append({
                    "name": name,
                    "path": filepath,
                    "size": stat.st_size,
                    "ext": ext.lstrip("."),
                    "modified": stat.st_mtime,
                })

    files.sort(key=lambda x: x["modified"], reverse=True)
    return {"files": files}


@app.get("/api/video/stream")
async def stream_video(file_path: str):
    """视频文件流式播放（支持 Range 请求）"""
    import os
    from fastapi.responses import FileResponse
    from fastapi import HTTPException

    if not os.path.isfile(file_path):
        raise HTTPException(404, "文件不存在")

    # 安全检查：只允许 output/videos 目录
    video_dir = os.path.join(os.getcwd(), "output", "videos")
    try:
        resolved = os.path.realpath(file_path)
        if not resolved.startswith(os.path.realpath(video_dir)):
            raise HTTPException(403, "无权访问此路径")
    except Exception:
        raise HTTPException(403, "路径检查失败")

    ext = os.path.splitext(file_path)[1].lower()
    media_types = {
        ".mp4": "video/mp4",
        ".webm": "video/webm",
        ".mkv": "video/x-matroska",
        ".flv": "video/x-flv",
        ".avi": "video/x-msvideo",
        ".m4v": "video/x-m4v",
        ".mp3": "audio/mpeg",
        ".m4a": "audio/mp4",
    }
    media_type = media_types.get(ext, "application/octet-stream")

    return FileResponse(
        file_path,
        media_type=media_type,
        filename=os.path.basename(file_path),
    )


@app.delete("/api/video/files")
async def delete_video_file(file_path: str):
    """删除视频文件"""
    import os
    from fastapi import HTTPException

    if not os.path.isfile(file_path):
        raise HTTPException(404, "文件不存在")

    video_dir = os.path.join(os.getcwd(), "output", "videos")
    try:
        resolved = os.path.realpath(file_path)
        if not resolved.startswith(os.path.realpath(video_dir)):
            raise HTTPException(403, "无权访问此路径")
    except Exception:
        raise HTTPException(403, "路径检查失败")

    os.remove(file_path)
    return {"success": True, "deleted": os.path.basename(file_path)}


# ==================== 启动入口 ====================

if __name__ == "__main__":
    import uvicorn
    settings = get_settings()
    uvicorn.run(
        "crawagent.api.server:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
    )
