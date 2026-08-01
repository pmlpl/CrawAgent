from __future__ import annotations

import asyncio
import json
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from crawagent.config.settings import get_settings
from crawagent.graph.agent_workflow import AgentRunner, get_agent_runner
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


class HarnessPromptResponse(BaseModel):
    """CrawlHarness prompt 响应"""
    session_id: str
    kind: str  # "completed" / "needs_input" / "error" / "cancelled"
    data: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


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
    
    # 如果是最终状态，从缓存移除（释放内存）
    if final and job.get("status") in ("completed", "failed"):
        del _job_cache[job_id]


async def _run_agent_background(job_id: str, instruction: str, seed_urls: List[str], thread_id: str, max_pages: int = 50, max_depth: int = 2):
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
        
        _job_cache[job_id]["logs"].append({"level": "INFO", "message": "初始化 Agent..."})
        
        runner = get_agent_runner()
        
        _job_cache[job_id]["logs"].append({"level": "INFO", "message": "开始执行工作流..."})
        
        last_log_count = 0
        final_items = []
        final_error = None
        final_pages = 0
        last_db_sync = 0
        
        try:
            async def _run_with_timeout():
                nonlocal final_items, final_error, final_pages, last_log_count, last_db_sync
                try:
                    async for state_chunk in runner.astream_state(
                        user_input=instruction, seed_urls=seed_urls, thread_id=thread_id,
                        max_pages=max_pages, max_depth=max_depth
                    ):
                        # state_chunk 是 dict: {node_name: state}
                        for node_name, state in state_chunk.items():
                            if not isinstance(state, dict):
                                continue
                            # 更新状态
                            job = state.get("current_job")
                            if job:
                                pages = job.stats.get("pages_crawled", 0)
                                total = job.plan.max_pages if job.plan else 1
                                items_count = job.stats.get("items_extracted", 0)
                                final_pages = pages
                                _job_cache[job_id]["progress"] = {
                                    "pages_crawled": pages,
                                    "pages_total": total,
                                }
                                _job_cache[job_id]["items_count"] = items_count
                            
                            # 同步日志
                            logs = state.get("logs", [])
                            if len(logs) > last_log_count:
                                new_logs = logs[last_log_count:]
                                _job_cache[job_id]["logs"].extend(new_logs)
                                last_log_count = len(logs)
                            
                            # 保存最终 items
                            if state.get("extracted_items"):
                                final_items = state["extracted_items"]
                            
                            if state.get("error_message"):
                                final_error = state["error_message"]
                            
                            # 每 10 秒同步一次到数据库
                            import time
                            now = time.time()
                            if now - last_db_sync > 10:
                                last_db_sync = now
                                # 异步同步，不阻塞
                                asyncio.create_task(_save_job_to_db(job_id))
                except Exception as e:
                    final_error = str(e)
                    raise
            
            await asyncio.wait_for(_run_with_timeout(), timeout=300)
        except asyncio.TimeoutError:
            final_error = "任务执行超时（5分钟）"
            raise RuntimeError(final_error)
        
        items_dict = [item.model_dump() if hasattr(item, "model_dump") else item for item in final_items]
        
        _job_cache[job_id].update({
            "status": "failed" if final_error else "completed",
            "progress": {"pages_crawled": final_pages, "pages_total": final_pages},
            "items_count": len(items_dict),
            "items": items_dict,
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
                        # ponytail: 无选择器时只保存基础元信息，不走不存在的 _fallback_generic
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

    result = await harness.prompt(session_id, req.message, lane_name=req.lane)

    return HarnessPromptResponse(
        session_id=session_id,
        kind=result.kind,
        data=result.data,
        error=result.error,
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
    # 安全检查：路径必须在 output_dir 下
    output_dir = Path("./output").resolve()
    abs_path = Path(path).resolve()
    try:
        abs_path.relative_to(output_dir)
    except ValueError:
        raise HTTPException(403, "路径越权：只能读取 output 目录下的文件")
    if not abs_path.is_file():
        raise HTTPException(404, f"文件不存在: {path}")
    try:
        with open(abs_path, "r", encoding="utf-8") as f:
            content = f.read()
        return {"path": path, "content": content, "size": len(content)}
    except Exception as e:
        raise HTTPException(500, f"读取失败: {e}")


@app.delete("/api/output/file")
async def delete_output_file(path: str):
    """删除已保存文件"""
    from pathlib import Path
    output_dir = Path("./output").resolve()
    abs_path = Path(path).resolve()
    try:
        abs_path.relative_to(output_dir)
    except ValueError:
        raise HTTPException(403, "路径越权：只能删除 output 目录下的文件")
    if not abs_path.is_file():
        raise HTTPException(404, f"文件不存在: {path}")
    try:
        abs_path.unlink()
        return {"deleted": True, "path": path}
    except Exception as e:
        raise HTTPException(500, f"删除失败: {e}")


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
