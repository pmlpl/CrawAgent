from __future__ import annotations

import os
# 禁用系统代理（必须在所有网络库导入前设置）
os.environ["NO_PROXY"] = "*"
os.environ["no_proxy"] = "*"

import sys
from pathlib import Path

import click
from loguru import logger

from crawagent.config.settings import get_settings
from crawagent.cli.main import cli


@click.group(invoke_without_command=True)
@click.option("--debug/--no-debug", default=False, help="开启调试模式")
@click.pass_context
def main(ctx, debug):
    """CrawAgent - 基于 LLM Agent 的智能爬虫系统"""
    if debug:
        logger.remove()
        logger.add(sys.stderr, level="DEBUG")
    else:
        logger.remove()
        logger.add(sys.stderr, level="INFO")
    
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


@main.command()
@click.argument("instruction", nargs=-1, required=True)
@click.option("--url", "-u", multiple=True, help="指定种子 URL")
@click.option("--max-pages", "-p", default=50, help="最大爬取页数")
@click.option("--thread", "-t", help="会话 ID（用于断点续爬）")
@click.option("--output", "-o", type=click.Path(), help="结果输出文件（JSON）")
def run(instruction, url, max_pages, thread, output):
    """交互式运行 Agent（Harness 执行路径）"""
    instruction_text = " ".join(instruction)
    
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
            # thread 兼容：复用为 harness session；不存在则创建
            session = await harness.get_session(thread) if thread else None
            if session is None:
                session = await harness.create_session(name=f"main:{instruction_text[:30]}")
            message = instruction_text
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
                        import json as _json
                        payload = _json.loads(e.content)
                        got = payload.get("items") if isinstance(payload, dict) else None
                        if isinstance(got, list):
                            items.extend(got)
                    except Exception:
                        pass

            click.echo(f"\n✅ 完成: {len(items)} 条数据")

            if output:
                import json
                Path(output).write_text(
                    json.dumps(items, ensure_ascii=False, indent=2)
                )
                click.echo(f"📄 结果已保存到: {output}")

            for i, item in enumerate(items[:5]):
                if not isinstance(item, dict):
                    item = getattr(item, "model_dump", lambda: item)()
                click.echo(f"  {i+1}. {item.get('title', 'N/A')} - {item.get('url', 'N/A')}")

            if len(items) > 5:
                click.echo(f"  ... 共 {len(items)} 条")
        finally:
            await harness.close()
    
    import asyncio
    asyncio.run(_run())


@main.command()
@click.argument("url")
@click.option("--output", "-o", type=click.Path(), help="输出文件")
def analyze(url, output):
    """分析单个站点结构"""
    async def _analyze():
        from crawagent.graph.site_analyzer import analyze_site
        analysis = await analyze_site(url)
        
        click.echo(f"\n🔍 站点分析: {url}")
        click.echo(f"  页面类型: {analysis.page_type.value}")
        click.echo(f"  数据来源: {analysis.data_source.value}")
        click.echo(f"  置信度: {analysis.confidence:.2f}")
        click.echo(f"  选择器: {analysis.selectors.model_dump()}")
        click.echo(f"  API 端点: {analysis.api_endpoint or 'N/A'}")
        click.echo(f"  分页: {analysis.pagination.model_dump()}")
        click.echo(f"  反爬: {[s.value for s in analysis.anti_bot_signs]}")
        
        if output:
            import json
            Path(output).write_text(analysis.model_dump_json(indent=2, ensure_ascii=False))
            click.echo(f"\n📄 分析结果已保存到: {output}")
    
    import asyncio
    asyncio.run(_analyze())


@main.command()
@click.argument("urls", nargs=-1, required=True)
@click.option("--selector", "-s", multiple=True, help="CSS 选择器 (格式: field=selector)")
@click.option("--max-pages", "-p", default=50)
@click.option("--output", "-o", type=click.Path())
def crawl(urls, selector, max_pages, output):
    """直接爬取（不经过 Agent 规划）"""
    async def _crawl():
        from crawagent.core.fetcher import Fetcher
        from crawagent.core.frontier import SQLiteFrontier
        from crawagent.core.extractor import DEFAULT_EXTRACTOR
        from crawagent.core.models import Selectors
        
        # 解析选择器
        selectors = {}
        for s in selector:
            if "=" in s:
                field, sel = s.split("=", 1)
                selectors[field] = sel
        
        frontier = SQLiteFrontier()
        await frontier.initialize()
        
        fetcher = Fetcher(frontier=frontier, per_domain_rate=0.5)
        extractor = DEFAULT_EXTRACTOR
        
        all_items = []
        
        try:
            await frontier.add_urls_batch([
                {"url": u, "depth": 0, "priority": 10} for u in urls
            ])
            
            if selectors:
                sel_obj = Selectors(**selectors)
            else:
                sel_obj = None
            
            async with fetcher:
                page_count = 0
                while page_count < max_pages:
                    records = await frontier.pop_next(limit=5)
                    if not records:
                        break
                    
                    results = await fetcher.fetch_batch([r.url for r in records])
                    
                    for rec, result in zip(records, results):
                        if isinstance(result, Exception) or not result.success:
                            continue
                        
                        if selectors:
                            items = DEFAULT_EXTRACTOR.extract(
                                url=result.url,
                                html=result.html,
                                selectors=sel_obj,
                                target_schema=None,
                                base_url=result.url,
                            )
                        else:
                            # 无选择器时只保存基础元信息（_fallback_generic 存在于 extractor.py，
                            # 但深爬场景直接走轻量元信息路径更高效）
                            items = [{"url": result.url, "title": result.title or "", "status": result.status_code}]

                        all_items.extend([item.model_dump() if hasattr(item, "model_dump") else item for item in items])
                        
                        if result.links:
                            await frontier.add_urls_batch([
                                {"url": link_url, "depth": rec.depth + 1, "parent_url": rec.url}
                                for link_url, _ in result.links[:20]
                            ])
                        
                        await frontier.mark_done(result.url, True)
                        page_count += 1
                        
                        if page_count >= max_pages:
                            break
        finally:
            await fetcher.close()
            await frontier.close()
        
        click.echo(f"\n✅ 完成: {len(all_items)} 条数据, {page_count} 页")
        
        if output:
            import json
            Path(output).write_text(json.dumps(all_items, ensure_ascii=False, indent=2))
            click.echo(f"📄 结果已保存到: {output}")
    
    import asyncio
    asyncio.run(_crawl())


@main.command()
@click.option("--host", default="0.0.0.0")
@click.option("--port", default=8000)
@click.option("--reload", is_flag=True)
def serve(host, port, reload):
    """启动 API 服务"""
    import uvicorn
    uvicorn.run("crawagent.api.server:app", host=host, port=port, reload=reload)


@main.command()
def init():
    """初始化数据目录"""
    settings = get_settings()
    for path in [settings.data_dir, "logs"]:
        Path(path).mkdir(parents=True, exist_ok=True)
    click.echo("[OK] 初始化完成")


@main.command()
def check():
    """检查依赖和配置"""
    settings = get_settings()
    
    click.echo("\n[CONFIG] 配置检查")
    click.echo(f"  数据目录: {settings.data_dir}")
    click.echo(f"  默认模型: {settings.default_model}")
    click.echo(f"  API 地址: {settings.openai_base_url}")
    click.echo(f"  并发数: {settings.max_concurrent}")
    click.echo(f"  限速: {settings.per_domain_rate} req/s")
    
    optional = {
        "curl_cffi": False,
        "playwright": False,
        "browserforge": False,
        "lxml": False,
    }
    
    try:
        import curl_cffi
        optional["curl_cffi"] = True
    except ImportError:
        pass
    
    try:
        import playwright
        optional["playwright"] = True
    except ImportError:
        pass
    
    try:
        import browserforge
        optional["browserforge"] = True
    except ImportError:
        pass
    
    try:
        import lxml
        optional["lxml"] = True
    except ImportError:
        pass
    
    click.echo(f"\n[PACKAGES] 可选依赖:")
    for name, installed in optional.items():
        status = "[OK]" if installed else "[MISSING]"
        click.echo(f"  {status} {name}")
    
    if not optional.get("playwright"):
        click.echo(f"\n[TIP] 提示: 运行 'playwright install chromium' 安装浏览器")


if __name__ == "__main__":
    main()