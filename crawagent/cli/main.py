from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import List, Optional

import click
from loguru import logger

from crawagent.config.settings import get_settings
from crawagent.graph.agent_workflow import AgentRunner
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
    """运行 Agent 爬取任务"""
    async def _run():
        runner = AgentRunner()
        result = await runner.run(
            user_input=instruction,
            seed_urls=list(url) if url else None,
            thread_id=thread_id,
        )
        
        items = result.get("extracted_items", [])
        click.echo(f"\n✅ 任务完成: {len(items)} 条数据")
        click.echo(f"Job ID: {result.get('job', {}).id if result.get('job') else 'N/A'}")
        
        # 显示前几条
        for item in items[:5]:
            click.echo(f"  - {item.get('title', 'N/A')} | {item.get('url', 'N/A')}")
        
        if len(items) > 5:
            click.echo(f"  ... 共 {len(items)} 条")
    
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
    """查看任务状态"""
    click.echo(f"任务 {job_id} 状态查询暂未实现")


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