"""视频资源发现子 Agent（工具包装式 / Agent-as-Tool）。

设计约束：
1. 子 Agent 工具集 5 个：web_search / probe_video_player / analyze_site_structure
   / browse_and_crawl / run_custom_script
2. 不传 checkpointer — 一次性执行，不跨轮记忆
3. 内部 tool_call/tool_result 不直接推前端 WS
4. 输出格式：精简 JSON（给主 Agent 结构化消费，不是给用户看的 Markdown 表格）
5. 【进度上报】内部以 .stream() 跑 agent，每完成一个关键 step 就调用
   crawagent.tools.progress.report_progress() 上报里程碑，server 心跳统一消费
   推前端。通用模块替代了旧版私有的 PROGRESS_INDEX。
"""
from __future__ import annotations

import json
import re
import time
from typing import Any

from langchain.agents import create_agent
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool

from crawagent.config.settings import get_settings
from crawagent.llm.model import get_llm
from crawagent.graph.middleware import TrimHistoryMiddleware
from crawagent.tools.progress import report_progress
from crawagent.tools.search_tool import web_search
from crawagent.tools.video_probe_tool import probe_video_player
from crawagent.tools.site_analyze_tool import analyze_site_structure
from crawagent.tools.browse_tool import browse_and_crawl
from crawagent.tools.script_tool import run_custom_script


# ---------------------------------------------------------------------------
# 子 Agent Prompt
# ---------------------------------------------------------------------------

FINDER_SYSTEM_PROMPT = """You are VideoFinder, a sub-agent that discovers and ranks third-party video sites.
Your output is consumed by the MAIN AGENT (not the user) — so output COMPACT JSON, not a pretty table.

TOOLS:
- web_search: find candidate sites
- probe_video_player: measure playback quality (resolution/speed/ads/m3u8)
- analyze_site_structure: check episode completeness, player type, site nav
- browse_and_crawl: render JS pages, find watch-page URLs from homepages
- run_custom_script: batch mode / last resort

WORKFLOW:
1. web_search("<title> 在线观看"). Get 8-12 results.
2. Filter out: douban, baike, bilibili, youtube, official platforms. Keep ~5 video aggregators.
3. For each candidate:
   a. If URL is a homepage, use browse_and_crawl to find a watch/detail page.
   b. probe_video_player(watch_url) → get resolution/speed/ads/m3u8.
   c. analyze_site_structure(detail_url) → get episode count + player type.
   Do max 2 probe attempts per site; skip if blocked (403/timeout).
4. Rank by: playable > episode_count > composite_score.

OUTPUT FORMAT — respond with ONLY this JSON (no markdown, no explanation):
{
  "query": "<the title>",
  "total_found": <int>,
  "probed": <int>,
  "ranked": [
    {
      "rank": 1,
      "site_name": "<short name>",
      "url": "<site homepage or detail page>",
      "watch_url": "<actual play page url>",
      "playable": true|false,
      "resolution": "<e.g. 1080p or null>",
      "speed_mbs": <float>,
      "episode_count": <int>,
      "player_type": "<dplayer|iframe|video|unknown>",
      "has_multiple_sources": true|false,
      "m3u8_url": "<url or null>",
      "popup_count": <int>,
      "iframe_count": <int>,
      "composite_score": <0-100>,
      "note": "<one short sentence>"
    }
  ],
  "failed": [
    {"site_name": "<name>", "url": "<url>", "reason": "<why it failed>"}
  ],
  "milestones": ["<step1 done>", "<step2 done>", "..."]
}

RULES:
- Keep each "note" under 30 words.
- Include a "milestones" array summarizing the key steps so the main agent can show progress summary.
- Only include sites you actually probed (not search results you didn't visit).
- If a site returned 403/timeout, put it in "failed", not "ranked".
- Never invent numbers — only report what probe_video_player returned.
- Output the JSON directly, no ```json wrapper, no preamble."""


def _create_video_finder():
    """每次调用新建子 Agent 实例。无 checkpointer、无中间件 — 一次性执行。"""
    s = get_settings()
    return create_agent(
        model=get_llm(s.video_finder_model),
        tools=[web_search, probe_video_player, analyze_site_structure,
               browse_and_crawl, run_custom_script],
        system_prompt=FINDER_SYSTEM_PROMPT,
        # 缓存双保险：截断工具结果 → 子代理自身缓存写入量降一个数量级，
        # 进一步降低对主 agent 缓存池的挤占；截断是确定性的，不破坏自身前缀缓存。
        middleware=[TrimHistoryMiddleware(tool_result_max_chars=2000)],
    )


# ---------------------------------------------------------------------------
# 从 agent.stream() 的 step 里抽取"用户可读的里程碑"摘要
# ---------------------------------------------------------------------------

_WSITE_RE = re.compile(r"https?://([^/\s]+)")


def _site_name(url: str) -> str:
    if not url:
        return ""
    m = _WSITE_RE.search(url)
    return m.group(1) if m else url[:40]


def _summarize_tool(name: str, args_str: str, result_raw: object) -> str | None:
    """返回一句话里程碑；返回 None 表示该 step 不值得上报（细节太多）。"""
    if name == "web_search":
        # 结果是一串文字，粗略数一下返回了多少条候选
        count = 0
        if isinstance(result_raw, str):
            # 用 [1] [2] ... 编号判断
            count = len(re.findall(r"^\s*\[\d+\]", result_raw, re.M)) or result_raw.count("\nhttp")
        return f"搜索完成：筛选后约 {max(count, 1)} 个候选站点"

    if name == "probe_video_player":
        site = _site_name(args_str)
        r = str(result_raw) if result_raw is not None else ""
        # 探测是否成功
        playable = "playable:true" in r.lower() or '"playable": true' in r or "resolution" in r.lower()
        blocked = "403" in r or "blocked" in r.lower() or "forbidden" in r.lower() or "TIMEOUT" in r.upper()
        if blocked:
            return f"探测 #{site}：被反爬拦截（403/超时），标记为失败"
        if playable:
            # 尝试抓 resolution / speed
            res_match = re.search(r"1080p|720p|480p|2160p|4k", r, re.I)
            spd_match = re.search(r"speed_mbs[\"']?\s*[:=]\s*(\d+[\.\d]*)", r)
            parts = []
            if res_match:
                parts.append(f"清晰度 {res_match.group(0)}")
            if spd_match:
                parts.append(f"{float(spd_match.group(1)):.2f} MB/s")
            info = f"（{', '.join(parts)}）" if parts else ""
            return f"探测 ✅ {site}{info}：可正常播放"
        return f"探测 #{site}：未找到可用播放源"

    if name == "analyze_site_structure":
        site = _site_name(args_str)
        r = str(result_raw) if result_raw is not None else ""
        m = re.search(r"\"episode_count\"\s*:\s*(\d+)", r)
        if m:
            return f"结构分析 {site}：共 {m.group(1)} 集，已确认"
        return f"结构分析 {site}：剧集列表信息待确认"

    if name == "browse_and_crawl":
        site = _site_name(args_str)
        return f"浏览 {site}：查找播放详情页…"

    if name == "run_custom_script":
        return "执行自定义脚本…"

    return None


def run_video_finder(query: str, _max_attempts: int = 2) -> str:
    """单轮 invoke（不重复跑），通过 agent.get_graph().stream() 边跑边采集 milestones。

    关键点：只用一次 invoke 拿最终 messages（避免"跑一次 stream + 一次 invoke"耗时翻倍）。
    为了在 invoke 过程中实时捕捉 tool 结果，使用 LangGraph 的 get_graph().stream()：
    stream 返回的 state 里包含"截至目前的完整 messages 列表"，我们对比前后差异
    识别出新的 ToolMessage，用 _summarize_tool 翻译为用户可读的里程碑。

    进度上报统一走 crawagent.tools.progress.report_progress()，
    由 agent.py 的 with_progress 包装器负责 start/finish 生命周期。
    """
    # LangGraph thread_id（仅用于 graph 状态隔离，不再用于进度追踪）
    thread_id = f"vf_{int(time.time() * 1000)}"
    # 本地收集所有里程碑，用于最终 JSON 的 milestones 字段（给主 Agent 消费）
    all_milestones: list[str] = []

    def _report(text: str) -> None:
        """同时上报到通用进度模块 + 本地收集。"""
        report_progress(text)
        all_milestones.append(text)

    _report(f"📡 开始搜索《{query}》的候选播放站点")

    last_err: Exception | None = None
    final_content = ""
    for attempt in range(1, _max_attempts + 1):
        try:
            agent = _create_video_finder()
            graph = agent.get_graph() if hasattr(agent, "get_graph") else None

            # 尝试用 graph.stream（如果 get_graph 存在）；否则回退到 invoke 后一次性扫描 messages
            last_ai_tool_calls: list = []
            seen_tool_ids: set = set()
            final_messages: list[BaseMessage] = []

            if graph is not None:
                try:
                    inputs = {"messages": [HumanMessage(content=query)]}
                    for state in graph.stream(
                        inputs,
                        {"recursion_limit": 80, "configurable": {"thread_id": thread_id}},
                        stream_mode="values",
                    ):
                        msgs = []
                        if isinstance(state, dict) and "messages" in state:
                            msgs = state["messages"]
                        elif isinstance(state, list):
                            msgs = [m for m in state if isinstance(m, BaseMessage)]
                        for m in msgs:
                            if isinstance(m, AIMessage):
                                last_ai_tool_calls = list(m.tool_calls or [])
                            if isinstance(m, ToolMessage) and m.tool_call_id not in seen_tool_ids:
                                seen_tool_ids.add(m.tool_call_id)
                                tc_name = m.name or ""
                                tc_args = ""
                                for tc in last_ai_tool_calls:
                                    if tc.get("id") == m.tool_call_id:
                                        tc_name = tc.get("name", tc_name)
                                        tc_args = json.dumps(
                                            tc.get("args", {}), ensure_ascii=False)
                                        break
                                if tc_name:
                                    summary = _summarize_tool(tc_name, tc_args, m.content)
                                    if summary:
                                        _report(summary)
                        final_messages = list(msgs)
                except Exception as e:
                    # graph.stream 失败，fallback 到 invoke
                    last_err = e
                    graph = None

            if graph is None:
                r = agent.invoke(
                    {"messages": [HumanMessage(content=query)]},
                    {"recursion_limit": 80},
                )
                final_messages = list(r.get("messages", []))
                # 一次性回扫所有 ToolMessage 做里程碑（虽然有点延迟但至少有）
                ai_tcs: list = []
                for m in final_messages:
                    if isinstance(m, AIMessage):
                        ai_tcs = list(m.tool_calls or [])
                    if isinstance(m, ToolMessage):
                        tc_name = m.name or ""
                        tc_args = ""
                        for tc in ai_tcs:
                            if tc.get("id") == m.tool_call_id:
                                tc_name = tc.get("name", tc_name)
                                tc_args = json.dumps(tc.get("args", {}), ensure_ascii=False)
                                break
                        if tc_name:
                            summary = _summarize_tool(tc_name, tc_args, m.content)
                            if summary and summary not in all_milestones:
                                _report(summary)

            # 从 final_messages 里取最后一条 AIMessage.content 作为最终 JSON
            for m in reversed(final_messages):
                if isinstance(m, AIMessage) and isinstance(m.content, str) and m.content.strip():
                    final_content = m.content
                    break
            break  # 成功跳出 attempt 循环

        except Exception as e:
            last_err = e
            _report(f"⚠️ attempt {attempt}/{_max_attempts} failed: {e}")
            final_content = ""

    # 收尾：错误兜底
    if last_err is not None and not final_content:
        # finish 由 with_progress 包装器负责，这里只返回错误 JSON
        return json.dumps({
            "error": str(last_err),
            "ranked": [],
            "failed": [],
            "milestones": all_milestones,
        }, ensure_ascii=False)

    if not final_content:
        fallback = {
            "query": query,
            "total_found": 0,
            "probed": 0,
            "ranked": [],
            "failed": [],
            "milestones": all_milestones,
            "error": "子 Agent 未返回有效 JSON",
        }
        final_content = json.dumps(fallback, ensure_ascii=False)

    # 如果没有 milestones 字段，补入
    try:
        obj = json.loads(final_content)
        if isinstance(obj, dict) and "milestones" not in obj:
            obj["milestones"] = all_milestones
            final_content = json.dumps(obj, ensure_ascii=False)
    except Exception:
        pass

    _report("✅ 全部探测完成，结果已汇总")
    # finish 由 with_progress 包装器在函数返回后自动调用
    return final_content


@tool
def video_site_expert(movie_query: str) -> str:
    """Delegate to a specialized sub-agent that finds third-party video sites and ranks them by quality.

    The sub-agent searches the web, probes each candidate's player quality (resolution,
    speed, ads, m3u8), checks episode completeness, and returns a COMPACT JSON report
    for the main agent to post-process (NOT a user-facing table).

    LIVE PROGRESS: during the sub-agent run, milestones are reported via
    crawagent.tools.progress.report_progress() — the server heartbeat consumes them
    and pushes to the frontend as progress events, so the user can see what's happening
    inside the sub-agent (search done, site 1 probed, site 2 blocked, ...) instead of
    staring at a "dig deep..." spinner for minutes.

    Use this when the user wants to watch a show/movie online and asks you to find
    WHERE to watch it and WHICH site is best.

    Args:
        movie_query: The show/movie to find, e.g. "师兄太稳健" or "Three Body season 1".
                     Do NOT include instructions — just the title.

    Returns:
        JSON string: {query, total_found, probed, ranked: [{rank, site_name, url,
        watch_url, playable, resolution, speed_mbs, episode_count, player_type,
        composite_score, note}], failed: [...], milestones: [...]}
    """
    try:
        return run_video_finder(movie_query)
    except Exception as e:
        return json.dumps({
            "error": str(e),
            "ranked": [],
            "failed": [],
            "milestones": [f"💥 子 Agent 异常：{e}"],
        }, ensure_ascii=False)
