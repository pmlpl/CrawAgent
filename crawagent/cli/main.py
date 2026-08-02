from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import List, Optional

import click
from loguru import logger

from crawagent.config.settings import get_settings
from crawagent.graph.site_analyzer import analyze_site
from crawagent.core.executor import CrawlExecutor
from crawagent.core.fetcher import Fetcher
from crawagent.core.frontier import SQLiteFrontier
from crawagent.core.extractor import DEFAULT_EXTRACTOR, create_dynamic_schema
from crawagent.core.models import CrawlPlan, Selectors, ExtractedItem


@click.group()
@click.option("--verbose", "-v", is_flag=True, help="详细日志")
@click.pass_context
def cli(ctx, verbose):
    """CrawAgent - 基于 LLM Agent 的智能爬虫系统"""
    ctx.ensure_object(dict)
    ctx.obj["verbose"] = verbose
    
    # 配置日志
    logger.remove()
    if verbose:
        logger.add(sys.stderr, level="DEBUG")
    else:
        logger.add(sys.stderr, level="INFO")


@cli.command()
@click.argument("instruction", required=True)
@click.option("--url", "-u", multiple=True, help="种子 URL（可多次指定）")
@click.option("--max-pages", "-p", default=50, help="最大爬取页数")
@click.option("--thread-id", "-t", help="会话 ID（用于断点续爬）")
def run(instruction: str, url: List[str], max_pages: int, thread_id: str):
    """运行 Agent 爬取任务（Harness 执行路径）"""
    async def _run():
        from crawagent.harness import (
            CrawlHarness, SessionManager, CrawlHooks,
            create_default_tools, create_antibot_hooks,
        )
        settings = get_settings()
        session_manager = SessionManager(settings.mysql_dsn)
        await session_manager.initialize()
        hooks = CrawlHooks()
        create_antibot_hooks(hooks)
        registry = create_default_tools()
        harness = CrawlHarness(
            session_manager=session_manager,
            hooks=hooks,
            tools=registry.list_tools(),
            tool_executors=registry.get_all_executors(),
        )
        try:
            # thread_id 兼容：复用为 harness session；不存在则创建
            session = await harness.get_session(thread_id) if thread_id else None
            if session is None:
                session = await harness.create_session(name=f"cli:{instruction[:30]}")
            message = instruction
            if url:
                message += "\n\n目标URL:\n" + "\n".join(url)
            task_notes = (
                "本次爬取任务的资源预算（严格遵守）：\n"
                f"- 最多抓取 {max_pages} 个页面（不要超过）"
            )
            result = await harness.prompt(session.session_id, message, task_notes=task_notes)

            if result.error:
                click.echo(f"❌ 任务失败: {result.error}")
                return

            # 从会话消息树提取 items（extract/save 工具返回 JSON 中的 items 数组）
            items = []
            try:
                entries = await session.get_entries(limit=1000, order="asc")
            except Exception:
                entries = []
            for e in entries:
                if e.role == "tool" and e.content:
                    try:
                        payload = json.loads(e.content)
                        got = payload.get("items") if isinstance(payload, dict) else None
                        if isinstance(got, list):
                            items.extend(got)
                    except Exception:
                        pass

            click.echo(f"\n✅ 任务完成: {len(items)} 条数据")
            click.echo(f"Session ID: {session.session_id}")

            # 显示前几条
            for item in items[:5]:
                if isinstance(item, dict):
                    click.echo(f"  - {item.get('title', 'N/A')} | {item.get('url', 'N/A')}")
                else:
                    click.echo(f"  - {item}")

            if len(items) > 5:
                click.echo(f"  ... 共 {len(items)} 条")
        finally:
            await harness.close()

    asyncio.run(_run())


@cli.command()
@click.argument("url", required=True)
def analyze(url: str):
    """分析站点结构"""
    async def _analyze():
        click.echo(f"🔍 分析站点: {url}")
        analysis = await analyze_site(url)
        
        click.echo(f"\n📊 分析结果:")
        click.echo(f"  页面类型: {analysis.page_type.value}")
        click.echo(f"  数据源: {analysis.data_source.value}")
        click.echo(f"  置信度: {analysis.confidence:.1%}")
        
        if analysis.selectors.list_container:
            click.echo(f"\n🎯 选择器:")
            click.echo(f"  列表容器: {analysis.selectors.list_container}")
            click.echo(f"  条目: {analysis.selectors.item}")
            click.echo(f"  标题: {analysis.selectors.title}")
            click.echo(f"  链接: {analysis.selectors.url}")
            for k, v in analysis.selectors.extra.items():
                click.echo(f"  {k}: {v}")
        
        if analysis.api_endpoint:
            click.echo(f"\n🔗 API 接口: {analysis.api_endpoint}")
            click.echo(f"  方法: {analysis.api_method}")
            click.echo(f"  参数: {analysis.api_params}")
        
        if analysis.pagination.type != "none":
            click.echo(f"\n📄 分页: {analysis.pagination.type.value}")
            if analysis.pagination.param:
                click.echo(f"  参数: {analysis.pagination.param}")
        
        if analysis.anti_bot_signs:
            click.echo(f"\n⚠️ 反爬迹象: {', '.join(s.value for s in analysis.anti_bot_signs)}")
    
    asyncio.run(_analyze())


@cli.command()
@click.argument("urls", nargs=-1, required=True)
@click.option("--selector", "-s", multiple=True, help="CSS 选择器: field=selector (如 title=.title a)")
@click.option("--schema", "-S", help="JSON Schema 文件路径")
@click.option("--max-pages", "-p", default=20, help="最大页数")
@click.option("--output", "-o", help="输出文件 (JSONL)")
def crawl(urls: List[str], selector: List[str], schema: str, max_pages: int, output: str):
    """直接爬取（不经过 Agent 规划）"""
    async def _crawl():
        # 解析选择器
        selectors = {}
        for s in selector:
            if "=" in s:
                k, v = s.split("=", 1)
                selectors[k] = v
        
        # 加载 Schema
        target_schema = {}
        if schema:
            with open(schema) as f:
                target_schema = json.load(f)
        
        DynamicItem = create_dynamic_schema(target_schema, "DirectItem") if target_schema else ExtractedItem
        selectors_obj = Selectors(**selectors) if selectors else None
        
        frontier = SQLiteFrontier()
        await frontier.initialize()
        
        fetcher = Fetcher(frontier=frontier, per_domain_rate=0.5)
        
        all_items = []
        
        try:
            async with fetcher:
                await frontier.add_urls_batch([
                    {"url": u, "depth": 0, "priority": 10} for u in urls
                ])
                
                page_count = 0
                while page_count < max_pages:
                    records = await frontier.pop_next(limit=5)
                    if not records:
                        break
                    
                    results = await fetcher.fetch_batch([r.url for r in records])
                    
                    for rec, result in zip(records, results):
                        if isinstance(result, Exception) or not result.success:
                            await frontier.mark_done(rec.url, False, str(result))
                            continue
                        
                        if selectors_obj and selectors_obj.item:
                            items = DEFAULT_EXTRACTOR.extract(
                                url=result.url,
                                html=result.html,
                                selectors=selectors_obj,
                                target_schema=DynamicItem,
                                base_url=result.url,
                            )
                        else:
                            items = DEFAULT_EXTRACTOR._fallback_generic(result, result.url)
                        
                        all_items.extend(items)
                        
                        # 发现新链接
                        if result.links and rec.depth < 2:
                            new_urls = [
                                {"url": link_url, "depth": rec.depth + 1, "parent_url": rec.url}
                                for link_url, _ in result.links[:20]
                            ]
                            await frontier.add_urls_batch(new_urls)
                        
                        await frontier.mark_done(result.url, True)
                        page_count += 1
                        
                        if page_count >= max_pages:
                            break
                            
        except Exception as e:
            logger.error(f"爬取失败: {e}")
            raise
        finally:
            await frontier.close()
        
        # 输出
        if output:
            with open(output, "w", encoding="utf-8") as f:
                for item in all_items:
                    f.write(json.dumps(item.model_dump() if hasattr(item, "model_dump") else item, ensure_ascii=False) + "\n")
            click.echo(f"\n✅ 已保存 {len(all_items)} 条到 {output}")
        else:
            for item in all_items[:10]:
                data = item.model_dump() if hasattr(item, "model_dump") else item
                click.echo(f"  {data.get('title', 'N/A')} | {data.get('url', 'N/A')}")
            click.echo(f"\n共 {len(all_items)} 条")
    
    asyncio.run(_crawl())


@cli.command()
@click.option("--host", default="0.0.0.0")
@click.option("--port", default=8000)
@click.option("--reload", is_flag=True)
def serve(host: str, port: int, reload: bool):
    """启动 API 服务"""
    import uvicorn
    uvicorn.run(
        "crawagent.api.server:app",
        host=host,
        port=port,
        reload=reload,
    )


@cli.command()
@click.argument("job_id")
def status(job_id: str):
    """查看任务状态（JobStore 持久化任务）"""
    import time

    from crawagent.core.job_store import get_job_store

    async def _status():
        job = await get_job_store().get_job(job_id)
        if not job:
            click.echo(f"任务 {job_id} 不存在")
            return

        def _fmt(ts: float) -> str:
            return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts)) if ts else "N/A"

        click.echo(f"任务 {job_id}")
        click.echo(f"  指令: {job.get('instruction') or 'N/A'}")
        seed_urls = job.get("seed_urls") or []
        click.echo(f"  种子 URL: {', '.join(seed_urls) if seed_urls else 'N/A'}")
        click.echo(f"  状态: {job.get('status')}")
        progress = job.get("progress") or {}
        click.echo(f"  进度: {progress if progress else '{}'}")
        click.echo(f"  结果数: {job.get('items_count') or 0}")
        if job.get("error"):
            click.echo(f"  错误: {job['error']}")
        click.echo(f"  创建: {_fmt(job.get('created_at') or 0)}")
        click.echo(f"  更新: {_fmt(job.get('updated_at') or 0)}")

    asyncio.run(_status())


@cli.command()
def check():
    """环境检查"""
    settings = get_settings()
    click.echo("[CONFIG] 环境检查:")
    click.echo(f"  配置目录: {settings.data_dir}")
    click.echo(f"  默认模型: {settings.default_model}")
    click.echo(f"  API 地址: {settings.openai_base_url}")
    
    # 检查可选依赖
    click.echo("[PACKAGES] 依赖检查:")
    try:
        import curl_cffi
        click.echo("  curl_cffi: [OK]")
    except ImportError:
        click.echo("  curl_cffi: [MISSING] (pip install curl_cffi)")
    
    try:
        import playwright
        click.echo("  playwright: [OK]")
    except ImportError:
        click.echo("  playwright: [MISSING] (pip install playwright && playwright install chromium)")
    
    try:
        import browserforge
        click.echo("  browserforge: [OK]")
    except ImportError:
        click.echo("  browserforge: [MISSING] (pip install browserforge)")
    from pathlib import Path
    for db in [settings.frontier_db_path, settings.retriever_db_path]:
        p = Path(db)
        click.echo(f"  {db}: {'存在' if p.exists() else '不存在'}")


if __name__ == "__main__":
    cli()