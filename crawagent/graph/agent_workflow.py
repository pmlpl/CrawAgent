"""真正的 Agent 工作流 - 使用 LangGraph StateGraph

核心架构: Think → Act → Observe → Reflect 循环

关键改进:
1. LLM 参意图解析和工具选择决策
2. 支持条件分支和循环（失败可重试/换工具）
3. LLM 反思执行结果，决定是否需要调整策略
"""
from __future__ import annotations

import concurrent.futures
import json
import re
from typing import Any, TypedDict, Annotated
from operator import add

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Send

from ..config.settings import Settings, get_logger

logger = get_logger(__name__)

from ..llm.factory import LLMFactory
from ..agent.tools import (
    TOOLS,
    TOOL_NAMES,
    TOOL_SYSTEM_PROMPT,
    _PACKET_AVAILABLE,
)
from ..agent.skill_loader import match_skill, load_all_skills
from ..tools import extract_urls


# ============================================================
# Agent 状态定义
# ============================================================

class AgentState(TypedDict):
    """Agent 工作流状态 - 支持累加和循环"""
    
    # 输入
    user_input: str
    iteration: int  # 当前迭代次数（防止无限循环）
    max_iterations: int  # 最大迭代次数
    
    # LLM 思考结果
    thought: str  # LLM 的思考过程
    selected_tool: str  # 选择的工具名称
    tool_args: dict[str, Any]  # 工具参数
    reasoning: str  # 选择该工具的理由
    
    # 执行结果
    tool_result: str  # 工具执行结果描述
    tool_success: bool  # 工具是否成功执行
    tool_output: Any  # 工具返回的原始数据
    
    # 观察结果
    observation: str  # 对执行结果的观察分析
    extracted_urls: list[str]  # 提取到的 URL
    extracted_data: list[dict[str, Any]]  # 提取到的结构化数据
    
    # 反思结果
    reflection: str  # LLM 对结果的反思
    is_satisfied: bool  # 是否满意当前结果
    needs_retry: bool  # 是否需要重试
    new_strategy: str  # 新的策略建议
    
    # 爬取相关状态（累加）
    crawl_results: list
    downloaded_images: list[dict[str, str]]
    downloaded_wallpapers: list
    scraped_videos: list
    packet_videos: Annotated[list[Any], add]
    packet_downloaded: Annotated[list[str], add]
    
    # 配置
    debug_mode: bool
    force_browser: bool | None
    image_output_dir: str
    video_output_dir: str
    max_wallpapers: int
    
    # 错误和最终输出
    errors: Annotated[list[str], add]
    final_reply: str
    
    # 追踪信息（用于 prompt）
    previous_tools: str  # 已尝试的工具列表
    previous_results: str  # 已获得的结果摘要

    # 并行执行相关
    parallel_mode: bool  # 是否为多 URL 并行模式
    parallel_results: list[str]  # 并行执行的结果列表（每次直接覆盖）


# ============================================================
# 工具列表和映射（从 agent.tools 导入）
# ============================================================

# 工具名 → @tool 函数映射
_TOOL_MAP = {t.name: t for t in TOOLS}


# ============================================================
# 节点 1: Decide - LLM 通过 bind_tools 选择并调用工具
# ============================================================

def node_decide(state: AgentState, llm_factory: LLMFactory) -> AgentState:
    """决策节点：LLM 通过 bind_tools 直接输出 tool_calls，不再解析 JSON。

    工作流程：
    1. 匹配 Skill：若用户输入匹配某个 Skill，注入其 prompt 到 SystemMessage
    2. LLM 根据用户输入，直接输出 tool_calls
    3. 读取 AIMessage.tool_calls 获取工具名和参数
    4. 检测多 URL：若多个 URL，标记 parallel_mode=True，tool_args 只含第一个 URL
    5. 备用：关键词匹配兜底（LLM 不支持 function calling 时）
    """

    urls = extract_urls(state["user_input"])

    # 清理 URL 中的反引号/引号包裹
    cleaned_urls = []
    for u in urls:
        # 去掉反引号、单引号、双引号
        cleaned = u.strip("`'\"")
        if cleaned and cleaned.startswith("http"):
            cleaned_urls.append(cleaned)
    urls = cleaned_urls
    if urls:
        state["extracted_urls"] = urls

    # 防止重复抓取：当前 URL 已抓过 → 强制满意
    existing_urls = {r.url for r in state.get("crawl_results", [])}
    if urls and any(u in existing_urls for u in urls):
        state["is_satisfied"] = True
        state["needs_retry"] = False
        state["final_reply"] = _build_final_reply(state)
        state["thought"] = "URL 已抓取过，直接生成最终回复"
        logger.info(f"\n  [Agent 决策] URL 已抓取过，直接结束")
        return state

    # 工具失败历史检测：如果上次 packet_crawler 失败，强制换 basic_crawler
    prev_tool = state.get("selected_tool", "")
    prev_success = state.get("tool_success", True)
    if prev_tool == "packet_crawler" and not prev_success:
        logger.info(f"\n  [Agent 决策] packet_crawler 上次失败，强制换 basic_crawler")
        state["selected_tool"] = "basic_crawler"
        state["tool_args"] = {"url": urls[0] if urls else ""}
        state["thought"] = "packet_crawler 失败后自动降级到 basic_crawler"
        return state

    # 多 URL 检测
    if len(urls) > 1:
        state["parallel_mode"] = True
        logger.info(f"\n  [Agent 决策] 检测到 {len(urls)} 个 URL，开启并行模式")
    else:
        state["parallel_mode"] = False

    # Skill 匹配
    skill = match_skill(state["user_input"])
    skill_prompt = ""
    if skill:
        skill_prompt = skill.to_system_prompt()
        logger.debug(f"  [Agent 决策] 匹配到 Skill: {skill.name}")

    # === 最高优先级：关键词快速判断是否为看视频请求
    # 注意：放在 LLM 决策之前直接判断，防止 LLM 不听话选 chat
    video_name = _extract_video_name(state["user_input"])
    if video_name and len(video_name) >= 2 and not urls:
        logger.debug(f"  [Agent 决策] 关键词匹配：看视频请求 -> watch_video({video_name})")
        state["selected_tool"] = "watch_video"
        state["tool_args"] = {"name": video_name}
        state["thought"] = f"关键词匹配：看视频 {video_name}"
        state["extracted_urls"] = urls
        return state

    try:
        llm = llm_factory.get_default()
        llm_with_tools = llm.bind_tools(TOOLS, tool_choice="auto")

        # 构造 System Prompt（含 Skill prompt）
        system_content = TOOL_SYSTEM_PROMPT + skill_prompt

        messages = [
            SystemMessage(content=system_content),
            HumanMessage(content=state["user_input"]),
        ]

        try:
            response = llm_with_tools.invoke(messages, config={"timeout": 30})
        except TimeoutError:
            raise TimeoutError("LLM 决策超时（30秒）")

        # 尝试从 response.tool_calls 读取结构化工具调用
        if response.tool_calls:
            tool_call = response.tool_calls[0]
            tool_name = tool_call["name"]
            tool_args = tool_call.get("args", {})

            # 如果 LLM 没有从输入中提取 URL，用 extract_urls 补上第一个
            if tool_name != "chat" and "url" not in tool_args and urls:
                tool_args["url"] = urls[0]

            state["selected_tool"] = tool_name
            state["tool_args"] = tool_args
            state["thought"] = f"工具调用：{tool_name}"
            state["extracted_urls"] = urls

            # === 修正：如果 LLM 选了 chat，但实际是看视频的请求，强制改为 watch_video ===
            if tool_name == "chat":
                import re
                user_text = state["user_input"]
                video_name = _extract_video_name(user_text)
                
                if video_name and len(video_name) >= 2 and not urls:
                    logger.debug(f"  [Agent 决策] 修正：LLM 选了 chat，但检测到看视频请求 → watch_video({video_name})")
                    state["selected_tool"] = "watch_video"
                    state["tool_args"] = {"name": video_name}
                    state["thought"] = f"修正为视频播放: {video_name}"

            # 追踪已用工具
            prev = state.get("previous_tools", "无")
            state["previous_tools"] = prev if prev == "无" else prev + ", " + tool_name

            logger.info(f"\n  [Agent 决策] 调用工具: {tool_name}")
            logger.debug(f"  [Agent 决策] 参数: {tool_args}")

        else:
            # LLM 未输出 tool_calls，降级到关键词兜底
            content = str(response.content)
            logger.warning(f"\n  [Agent 决策] LLM 未输出 tool_calls，降级到关键词匹配")
            logger.debug(f"  [Agent 决策] LLM 回复: {content[:100]}...")
            decision = _fallback_decision(state["user_input"], urls)
            state["selected_tool"] = decision["selected_tool"]
            state["tool_args"] = decision["tool_args"]
            state["thought"] = f"关键词兜底: {decision['reasoning']}"
            state["extracted_urls"] = urls
            state["reasoning"] = decision.get("reasoning", "")

    except TimeoutError as e:
        logger.error(f"  [Agent 决策] LLM 调用超时: {e}，使用关键词兜底")
        decision = _fallback_decision(state["user_input"], urls)
        state["selected_tool"] = decision["selected_tool"]
        state["tool_args"] = decision["tool_args"]
        state["thought"] = f"超时降级: {e}"
        state["extracted_urls"] = urls
        state["errors"].append(f"决策节点超时: {e}")
    except Exception as e:
        logger.error(f"  [Agent 决策] LLM 调用失败: {e}，使用关键词兜底")
        decision = _fallback_decision(state["user_input"], urls)
        state["selected_tool"] = decision["selected_tool"]
        state["tool_args"] = decision["tool_args"]
        state["thought"] = f"异常降级: {e}"
        state["extracted_urls"] = urls
        state["errors"].append(f"决策节点失败: {e}")

    return state


def _extract_video_name(text: str) -> str | None:
    """从用户输入中提取视频名称

    支持的说法:
    - 我想看《XXX》
    - 播放XXX
    - 看XXX电视剧/电影/综艺/动漫
    - 打开XXX
    - 帮我找XXX
    - 有没有XXX
    """
    import re

    text = text.strip()
    if not text:
        return None

    # 触发词模式（从开头匹配）
    trigger_patterns = [
        r'^(?:我想|我要|帮我|给我|能不能|可以|请)?(?:搜索一下|搜索|搜一下|看一下|找一下|播放一下|放一下|看|播放|放|打开|找|搜|查)(?:一下|个|部|一[部个])?\s*[《\"\']?',
        r'^有没有\s*[《\"\']?',
        r'^给我来个\s*[《\"\']?',
    ]

    remaining = text
    matched = False
    for pat in trigger_patterns:
        match = re.match(pat, text)
        if match:
            remaining = text[match.end():]
            matched = True
            break

    if not matched:
        return None

    # 去掉结尾的类型词
    remaining = re.sub(
        r'(?:电视剧|电影|综艺|动漫|动画片|国产剧|港剧|韩剧|美剧|日剧|剧|片|视频|节目|栏目|真人秀)\s*$',
        '',
        remaining
    )
    # 去掉所有书名号、引号和空白
    remaining = remaining.replace('《', '').replace('》', '')
    remaining = remaining.replace('【', '').replace('】', '')
    remaining = remaining.replace('「', '').replace('」', '')
    remaining = remaining.replace('"', '').replace("'", '')
    remaining = remaining.strip()

    # 排除无意义的词
    exclude_keywords = [
        "你", "我", "他", "她", "它",
        "这个", "那个", "什么", "为什么", "怎么", "哪里", "怎样",
        "吗", "呢", "啊", "吧", "哦",
    ]

    if len(remaining) >= 2 and remaining not in exclude_keywords:
        if not any(remaining == kw for kw in exclude_keywords):
            return remaining

    return None


def _fallback_decision(user_input: str, urls: list[str]) -> dict:
    """备用决策策略（关键词匹配）"""
    
    text_lower = user_input.lower()
    
    # === 最高优先级：看视频 ===
    video_name = _extract_video_name(user_input)

    if video_name and len(video_name) >= 2 and not urls:
        return {
            "intent": "watch_video",
            "selected_tool": "watch_video",
            "tool_args": {"name": video_name},
            "reasoning": f"检测到看视频请求，视频名: {video_name}",
        }
    
    # 没有 URL → chat
    if not urls:
        return {
            "intent": "chat",
            "selected_tool": "chat",
            "tool_args": {"message": user_input},
            "reasoning": "用户输入中没有 URL，进入对话模式",
        }
    
    # 视频网站关键词（兜底策略：先用 basic_crawler 抓 HTML 文本）
    # 原因：packet_crawler 当前有 'ChromiumPageSetter' object has no attribute 'headless' bug，
    #       而 basic_crawler 用 httpx 即可，对 B站静态页面已能拿到主要信息（标题、描述、UP主）。
    video_keywords = ("抖音", "douyin", "bilibili", "b站", "优酷", "youku",
                      "爱奇艺", "iqiyi", "腾讯视频", "v.qq", "youtube")
    if any(kw in text_lower or kw in urls[0].lower() for kw in video_keywords):
        return {
            "intent": "crawl",
            "selected_tool": "basic_crawler",
            "tool_args": {"url": urls[0]},
            "reasoning": "检测到视频网站（兜底策略），先尝试 basic_crawler 抓取 HTML 文本",
        }
    
    # 壁纸网站关键词
    wallpaper_keywords = ("壁纸", "wallpaper", "haowallpaper", "hwallpaper")
    if any(kw in text_lower or kw in urls[0].lower() for kw in wallpaper_keywords):
        return {
            "intent": "download",
            "selected_tool": "wallpaper_scraper",
            "tool_args": {"url": urls[0]},
            "reasoning": "检测到壁纸网站，使用智能壁纸爬虫",
        }
    
    # 图片下载关键词
    image_keywords = ("下载图片", "图片", "image", "photo", "保存图片")
    if any(kw in text_lower for kw in image_keywords):
        return {
            "intent": "download",
            "selected_tool": "image_downloader",
            "tool_args": {"url": urls[0]},
            "reasoning": "用户请求下载图片",
        }
    
    # 默认：基础爬虫
    return {
        "intent": "crawl",
        "selected_tool": "basic_crawler",
        "tool_args": {"url": urls[0]},
        "reasoning": "普通网页，使用基础 HTTP 爬虫",
    }


# ============================================================
# ============================================================
# 节点 2: Act - 执行选定的工具（直接调用 @tool 函数）
# ============================================================

def node_act(state: AgentState) -> AgentState:
    """执行工具节点：直接调用 @tool 函数"""

    tool_name = state["selected_tool"]
    tool_args = state["tool_args"]

    logger.info(f"\n  [Agent 执行] 工具: {tool_name}")
    logger.debug(f"  [Agent 执行] 参数: {tool_args}")

    try:
        tool_fn = _TOOL_MAP.get(tool_name)
        if tool_fn is None:
            state["tool_result"] = f"❌ 未知工具: {tool_name}"
            state["tool_success"] = False
            logger.error(f"  [Agent 执行] 未知工具: {tool_name}")
            return state

        # 直接调用 @tool 函数
        result_str = tool_fn.invoke(tool_args)

        # 检查结果是否为失败（工具返回错误字符串而非抛异常）
        is_failure = (
            result_str.startswith("❌") or
            result_str.startswith("抓取失败") or
            "失败" in result_str[:50] or
            "error" in result_str.lower()[:50]
        )

        state["tool_result"] = result_str
        state["tool_success"] = not is_failure
        state["tool_output"] = result_str

        # 提取 basic_crawler/browser_crawler 的详细内容到 crawl_results
        url = tool_args.get("url", "")
        if tool_name in ("basic_crawler", "browser_crawler") and url:
            try:
                from ..tools import BaseCrawler as _BC
                detail = _BC().fetch(url, extra_headers=tool_args.get("extra_headers"))
                if detail.success:
                    # 覆盖该 URL 的旧结果（避免重复）
                    state["crawl_results"] = [
                        r for r in state["crawl_results"] if r.url != url
                    ]
                    state["crawl_results"].append(detail)
            except Exception as e:
                logger.debug(f"  [Agent 执行] 提取详细结果失败: {e}")

        logger.debug(f"  [Agent 执行] 结果: {result_str[:120]}...")

    except TimeoutError as e:
        state["tool_success"] = False
        state["tool_result"] = f"❌ 工具执行超时: {e}"
        state["tool_output"] = None
        state["errors"].append(f"工具 {tool_name} 执行超时: {e}")
        logger.error(f"  [Agent 执行] 超时: {e}")
    except Exception as e:
        state["tool_success"] = False
        state["tool_result"] = f"❌ 工具执行异常: {e}"
        state["tool_output"] = None
        state["errors"].append(f"工具 {tool_name} 执行失败: {e}")
        logger.error(f"  [Agent 执行] 异常: {e}")

    return state


# ============================================================
# 节点 2B: Parallel Act - 多 URL 并行执行
# ============================================================

def node_parallel_act(state: AgentState) -> AgentState:
    """并行执行节点：对多个 URL 同时调用同一个工具。

    使用 ThreadPoolExecutor 并行执行，最大 5 个线程。
    每个 URL 单独调用 tool_fn.invoke()，结果存入 parallel_results。
    """

    tool_name = state["selected_tool"]
    urls = state.get("extracted_urls", [])
    base_args = state["tool_args"].copy()

    tool_fn = _TOOL_MAP.get(tool_name)
    if tool_fn is None:
        state["tool_result"] = f"❌ 未知工具: {tool_name}"
        state["tool_success"] = False
        return state

    # 构建每个 URL 的参数
    tasks = []
    for url in urls:
        args = base_args.copy()
        args["url"] = url
        tasks.append(args)

    logger.info(f"\n  [Agent 并行执行] 工具: {tool_name}, URL 数: {len(urls)}")

    try:
        # 最多 5 个线程并行
        max_workers = min(len(urls), 5)
        results = []

        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(tool_fn.invoke, args): url
                for url, args in zip(urls, tasks)
            }
            for future in concurrent.futures.as_completed(futures):
                url = futures[future]
                try:
                    result_str = future.result()
                    results.append(result_str)
                    logger.debug(f"    ✓ {url[:60]}: {result_str[:80]}...")
                except TimeoutError as exc:
                    err = f"❌ {url}: 超时"
                    results.append(err)
                    state["errors"].append(f"并行执行 {url} 超时")
                    logger.error(f"    ✗ {url}: 超时")
                except Exception as exc:
                    err = f"❌ {url}: {exc}"
                    results.append(err)
                    state["errors"].append(f"并行执行 {url} 失败: {exc}")
                    logger.error(f"    ✗ {url}: {exc}")

        # 直接覆盖 parallel_results（不再累加）
        state["parallel_results"] = results

        # 汇总结果
        summary = f"并行执行完成，共 {len(urls)} 个 URL，成功 {sum(1 for r in results if not r.startswith('❌'))} 个"
        state["tool_result"] = summary
        state["tool_success"] = True
        state["tool_output"] = results

        logger.info(f"  [Agent 并行执行] 汇总: {summary}")

    except (TimeoutError, concurrent.futures.TimeoutError) as e:
        state["tool_success"] = False
        state["tool_result"] = f"❌ 并行执行超时: {e}"
        state["errors"].append(f"并行执行超时: {e}")
        logger.error(f"  [Agent 并行执行] 超时: {e}")
    except Exception as e:
        state["tool_success"] = False
        state["tool_result"] = f"❌ 并行执行异常: {e}"
        state["errors"].append(f"并行执行失败: {e}")
        logger.error(f"  [Agent 并行执行] 异常: {e}")

    return state


# ============================================================
# 条件分支：根据 parallel_mode 路由到 act 或 parallel_act
# ============================================================

def should_parallelize(state: AgentState) -> str:
    """根据 parallel_mode 决定走单 URL 路径还是多 URL 并行路径"""
    if state.get("parallel_mode"):
        logger.debug(f"\n  [Agent 路由] 多 URL → parallel_act")
        return "parallel_act"
    else:
        logger.debug(f"\n  [Agent 路由] 单 URL → act")
        return "act"


# ============================================================
# 节点 3: Observe - 观察执行结果
# ============================================================

def node_observe(state: AgentState, llm_factory: LLMFactory) -> AgentState:
    """观察结果节点（快速模式：不调 LLM，直接根据结果生成观察结论）"""
    
    tool_name = state["selected_tool"]
    tool_result = state["tool_result"]
    tool_success = state["tool_success"]
    data_summary = _build_data_summary(state)
    
    # 快速生成观察结论（不调 LLM，避免等待）
    if tool_success:
        if tool_result.startswith("✅"):
            state["observation"] = (
                f"✅ {tool_name} 执行成功。{data_summary or tool_result[:100]}"
            )
        elif tool_result.startswith("🎬"):
            state["observation"] = f"🎬 {tool_name} 识别到视频内容。{data_summary or tool_result[:100]}"
        else:
            state["observation"] = f"✅ {tool_name} 完成：{tool_result[:200]}"
    else:
        state["observation"] = f"❌ {tool_name} 执行失败：{tool_result[:200]}"

    logger.debug(f"\n  [Agent 观察] {state['observation'][:150]}...")
    return state


def _build_data_summary(state: AgentState) -> str:
    """构建数据摘要"""
    lines = []
    
    if state["crawl_results"]:
        lines.append(f"爬取页面: {len(state['crawl_results'])} 个")
        for r in state["crawl_results"][:3]:
            lines.append(f"  - {r.url}: {r.title or '(无标题)'}")
    
    if state["downloaded_images"]:
        success = sum(1 for img in state["downloaded_images"] if img.get("path"))
        lines.append(f"下载图片: {success}/{len(state['downloaded_images'])} 张成功")
    
    if state["downloaded_wallpapers"]:
        lines.append(f"下载壁纸: {len(state['downloaded_wallpapers'])} 个")
    
    if state["scraped_videos"]:
        lines.append(f"视频元数据: {len(state['scraped_videos'])} 个")
        for v in state["scraped_videos"][:2]:
            lines.append(f"  - {v.title or '(无标题)'}")
    
    if state["packet_videos"]:
        lines.append(f"抓包视频: {len(state['packet_videos'])} 个")
    
    if state["packet_downloaded"]:
        lines.append(f"已下载视频文件: {len(state['packet_downloaded'])} 个")
    
    return "\n".join(lines) or "未获取到任何数据"


# ============================================================
# 节点 4: Reflect - 反思并决定下一步
# ============================================================

REFLECT_PROMPT = """你是一个智能爬虫 Agent。刚刚观察了执行结果，现在需要反思并决定下一步。

## 当前状态
- 用户请求: {user_input}
- 已迭代次数: {iteration}/{max_iterations}
- 使用的工具: {selected_tool}
- 执行结果: {tool_result}
- 观察分析: {observation}
- 已获取数据: {data_summary}
- 遇到的错误: {errors}

## 你的任务
1. 判断当前结果是否满足用户需求
2. 如果不满足，分析原因（工具选择错误？参数不对？网站反爬？）
3. 决定是否需要重试或换用其他工具
4. 如果需要重试，给出新的策略

## 输出格式（JSON）
```json
{{"reflection": "对整体过程的反思...", "is_satisfied": true, "needs_retry": false, "reason": "满意/不满意的原因", "new_strategy": "", "new_tool": "", "new_args": {{}}, "final_answer": "如果满意，给用户的最终回复"}}
```

注意：
- 如果已迭代超过 {max_iterations} 次，必须返回 is_satisfied=true
- 如果获取到了有意义的数据，倾向于满意
- 如果只是小问题（如部分图片下载失败），可以满意并给出说明
"""


def node_reflect(state: AgentState, llm_factory: LLMFactory) -> AgentState:
    """反思节点：决定是否满意或需要重试
    
    优化策略：
    1. 简单任务（工具成功）→ 直接判断满意，不调用 LLM
    2. 失败任务 → 调用 LLM 分析原因
    3. 复杂任务（需要重试）→ 调用 LLM 决策
    """
    
    data_summary = _build_data_summary(state)
    tool_success = state.get("tool_success", False)
    tool_name = state.get("selected_tool", "")
    iteration = state.get("iteration", 0)
    max_iterations = state.get("max_iterations", 3)
    
    # ============ 快速路径：简单成功任务 ============
    # 如果工具执行成功，直接判断满意，不调用 LLM（节省时间）
    if tool_success and tool_name in ("basic_crawler", "browser_crawler", "image_downloader", "wallpaper_scraper", "video_metadata_scraper"):
        state["reflection"] = f"{tool_name} 执行成功，直接判断满意"
        state["is_satisfied"] = True
        state["needs_retry"] = False
        state["final_reply"] = _build_final_reply(state)
        logger.debug(f"\n  [Agent 反思] 快速路径 - 工具成功，直接满意")
        logger.debug(f"  [Agent 反思] 反思结果: {state['reflection']}")
        return state
    
    # ============ 中速路径：工具失败或复杂任务 ============
    # 只有在失败或需要高级决策时才调用 LLM
    
    # 如果已达最大迭代，必须结束
    if iteration >= max_iterations:
        state["reflection"] = f"已达到最大迭代次数 {max_iterations}，强制结束"
        state["is_satisfied"] = True
        state["needs_retry"] = False
        state["final_reply"] = _build_final_reply(state)
        logger.debug(f"\n  [Agent 反思] 达到最大迭代，强制结束")
        return state
    
    # 失败任务，尝试用 LLM 分析原因
    if not tool_success:
        logger.debug(f"\n  [Agent 反思] 工具执行失败，调用 LLM 分析...")
        
        prompt = REFLECT_PROMPT.format(
            user_input=state["user_input"],
            iteration=iteration,
            max_iterations=max_iterations,
            selected_tool=tool_name,
            tool_result=state.get("tool_result", ""),
            observation=state.get("observation", ""),
            data_summary=data_summary,
            errors="\n".join(state.get("errors", [])) or "无",
        )
        
        try:
            llm = llm_factory.get_default()

            try:
                response = llm.invoke([
                    SystemMessage(content="你是一个决策专家，擅长评估任务完成度并制定下一步策略。"),
                    HumanMessage(content=prompt),
                ], config={"timeout": 60})
            except TimeoutError:
                raise TimeoutError("LLM 调用超时（60秒）")
            
            content = str(response.content)
            
            # 尝试解析 JSON
            reflection_data = None
            
            # 方法1: 从 markdown 代码块提取
            code_block_match = re.search(r'```(?:json)?\s*\n?([\s\S]*?)\n?```', content)
            if code_block_match:
                try:
                    reflection_data = json.loads(code_block_match.group(1).strip())
                except json.JSONDecodeError:
                    pass
            
            # 方法2: 匹配完整 JSON 对象
            if reflection_data is None:
                first_brace = content.find('{')
                last_brace = content.rfind('}')
                if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
                    try:
                        reflection_data = json.loads(content[first_brace:last_brace + 1])
                    except json.JSONDecodeError:
                        pass
            
            # 方法3: 默认满意（防止无限循环）
            if reflection_data is None:
                reflection_data = {"reflection": content, "is_satisfied": True}
            
            state["reflection"] = reflection_data.get("reflection", "")
            state["is_satisfied"] = reflection_data.get("is_satisfied", True)
            state["needs_retry"] = reflection_data.get("needs_retry", False)
            state["new_strategy"] = reflection_data.get("new_strategy", "")
            
            # 如果需要重试，更新工具选择
            if state["needs_retry"] and reflection_data.get("new_tool"):
                state["selected_tool"] = reflection_data["new_tool"]
                state["tool_args"] = reflection_data.get("new_args", state["tool_args"])
            
            # 生成最终回复
            if state["is_satisfied"] or state.get("crawl_results"):
                llm_final = reflection_data.get("final_answer", "")
                built = _build_final_reply(state)
                state["final_reply"] = llm_final if llm_final and llm_final.strip() else built
                state["is_satisfied"] = True
                state["needs_retry"] = False
            
            logger.debug(f"\n  [Agent 反思] 满意: {state['is_satisfied']}")
            logger.debug(f"  [Agent 反思] 需重试: {state['needs_retry']}")
            if state['reflection']:
                logger.debug(f"  [Agent 反思] {state['reflection'][:100]}...")
            
        except TimeoutError as e:
            # 反思超时，默认满意
            state["reflection"] = f"反思超时: {e}"
            state["is_satisfied"] = True
            state["needs_retry"] = False
            state["final_reply"] = _build_final_reply(state)
            state["errors"].append(f"反思节点超时: {e}")
            logger.error(f"\n  [Agent 反思] 超时: {e}，默认满意")
        except Exception as e:
            # 反思失败，默认满意
            state["reflection"] = f"反思失败: {e}"
            state["is_satisfied"] = True
            state["needs_retry"] = False
            state["final_reply"] = _build_final_reply(state)
            state["errors"].append(f"反思节点失败: {e}")
            logger.error(f"\n  [Agent 反思] 异常: {e}，默认满意")
    
    else:
        # 其他情况，也直接满意
        state["reflection"] = "任务完成"
        state["is_satisfied"] = True
        state["needs_retry"] = False
        state["final_reply"] = _build_final_reply(state)
    
    return state


def _build_final_reply(state: AgentState) -> str:
    """构建最终回复"""
    lines = []
    
    lines.append("🕷️ 爬取结果")
    lines.append("=" * 40)
    
    # 爬取页面
    if state["crawl_results"]:
        for r in state["crawl_results"]:
            if r.success:
                lines.append(f"✅ {r.url} (HTTP {r.status_code})")
                if r.title:
                    lines.append(f"   标题: {r.title}")
                # 显示文本内容预览
                if r.text and r.text.strip():
                    preview = r.text[:400].strip()
                    if len(r.text) > 400:
                        preview += "..."
                    lines.append(f"\n📄 内容预览:")
                    lines.append(preview)
            else:
                lines.append(f"❌ {r.url} - {r.error}")
    
    # 抓包视频
    if state["packet_videos"]:
        lines.append(f"\n🎬 抓包提取: {len(state['packet_videos'])} 个视频")
        lines.append(f"   保存位置: {state['video_output_dir']}/")
        for i, v in enumerate(state["packet_videos"][:5], 1):
            title = getattr(v, "title", "") or f"视频_{i}"
            lines.append(f"   {i}. {title[:60]}")
    
    # 下载的壁纸
    if state["downloaded_wallpapers"]:
        lines.append(f"\n🖼️ 壁纸下载: {len(state['downloaded_wallpapers'])} 个")
        lines.append(f"   保存位置: {state['image_output_dir']}/")
        for i, wp in enumerate(state["downloaded_wallpapers"][:5], 1):
            lines.append(f"   {i}. {wp.filename}")
    
    # 下载的图片
    if state["downloaded_images"]:
        success = sum(1 for img in state["downloaded_images"] if img.get("path"))
        lines.append(f"\n📷 图片下载: {success}/{len(state['downloaded_images'])} 张成功")
        lines.append(f"   保存位置: {state['image_output_dir']}/")
    
    # 视频元数据
    if state["scraped_videos"]:
        lines.append(f"\n📺 视频元数据: {len(state['scraped_videos'])} 个")
        for v in state["scraped_videos"][:3]:
            lines.append(f"   - {v.title or '(无标题)'}")
            if v.stream_urls:
                lines.append(f"     流地址: {len(v.stream_urls)} 个")
    
    # 错误
    if state["errors"]:
        lines.append("\n⚠️ 遇到的问题:")
        for err in state["errors"][:5]:
            lines.append(f"   - {err}")
    
    # 提示
    if state["crawl_results"] or state["downloaded_images"] or state["packet_videos"]:
        lines.append("\n💡 使用 /export 可导出数据")
    
    return "\n".join(lines)


# ============================================================
# 条件分支函数
# ============================================================

def should_continue(state: AgentState) -> str:
    """判断是否继续循环"""
    
    # 达到最大迭代次数，必须结束
    if state["iteration"] >= state["max_iterations"]:
        logger.debug(f"\n  [Agent] 达到最大迭代次数 {state['max_iterations']}, 结束循环")
        return "end"
    
    # 用户满意，结束
    if state["is_satisfied"]:
        logger.info(f"\n  [Agent] 任务完成，满意结束")
        return "end"
    
    # 需要重试，继续
    if state["needs_retry"]:
        logger.debug(f"\n  [Agent] 需要重试，进入下一轮迭代")
        return "retry"
    
    # 默认结束
    return "end"


def increment_iteration(state: AgentState) -> AgentState:
    """增加迭代计数"""
    state["iteration"] = state["iteration"] + 1
    return state


# ============================================================
# Agent 工作流类
# ============================================================

class AgentWorkflow:
    """真正的 Agent 工作流 - 使用 LangGraph StateGraph"""
    
    def __init__(self, settings: Settings, llm_factory: LLMFactory):
        self.settings = settings
        self.llm_factory = llm_factory
        self.graph = self._build_graph()
    
    def _build_graph(self) -> StateGraph:
        """构建 LangGraph 状态图"""
        
        # 创建状态图
        graph = StateGraph(AgentState)
        
        # 添加节点
        graph.add_node("decide", lambda s: node_decide(s, self.llm_factory))
        graph.add_node("act", node_act)
        graph.add_node("parallel_act", node_parallel_act)
        graph.add_node("observe", lambda s: node_observe(s, self.llm_factory))
        graph.add_node("reflect", lambda s: node_reflect(s, self.llm_factory))
        graph.add_node("increment", increment_iteration)

        # 设置入口
        graph.set_entry_point("decide")

        # 条件边：decide → (act | parallel_act)，由 should_parallelize 决定
        graph.add_conditional_edges(
            "decide",
            should_parallelize,
            {
                "act": "act",
                "parallel_act": "parallel_act",
            }
        )

        # act 和 parallel_act 都通向 observe
        graph.add_edge("act", "observe")
        graph.add_edge("parallel_act", "observe")
        graph.add_edge("observe", "reflect")
        graph.add_edge("reflect", "increment")

        # 条件边：increment → (decide | END)，由 should_continue 决定
        graph.add_conditional_edges(
            "increment",
            should_continue,
            {
                "end": END,
                "retry": "decide",
            }
        )
        
        return graph.compile()
    
    def run(
        self,
        user_input: str,
        *,
        debug_mode: bool = False,
        force_browser: bool | None = None,
        max_wallpapers: int = 12,
        image_output_dir: str = "output/img",
        video_output_dir: str = "output/video",
        max_iterations: int = 3,
    ) -> AgentState:
        """执行 Agent 工作流
        
        Args:
            user_input: 用户输入
            debug_mode: 是否显示浏览器窗口
            force_browser: None=自动, True=强制浏览器, False=只用httpx
            max_wallpapers: 壁纸最大下载数
            image_output_dir: 图片输出目录
            video_output_dir: 视频输出目录
            max_iterations: 最大迭代次数（防止无限循环）
        """
        
        # 初始化状态
        initial_state: AgentState = {
            "user_input": user_input,
            "iteration": 0,
            "max_iterations": max_iterations,
            "thought": "",
            "selected_tool": "",
            "tool_args": {},
            "reasoning": "",
            "tool_result": "",
            "tool_success": False,
            "tool_output": None,
            "observation": "",
            "extracted_urls": [],
            "extracted_data": [],
            "reflection": "",
            "is_satisfied": False,
            "needs_retry": False,
            "new_strategy": "",
            "crawl_results": [],
            "downloaded_images": [],
            "downloaded_wallpapers": [],
            "scraped_videos": [],
            "packet_videos": [],
            "packet_downloaded": [],
            "debug_mode": debug_mode,
            "force_browser": force_browser,
            "image_output_dir": image_output_dir,
            "video_output_dir": video_output_dir,
            "max_wallpapers": max_wallpapers,
            "errors": [],
            "final_reply": "",
            "previous_tools": "无",
            "previous_results": "无",
            "parallel_mode": False,
            "parallel_results": [],
        }
        
        logger.info(f"\n{'='*60}")
        logger.info(f"🤖 Agent 工作流启动")
        logger.info(f"{'='*60}")
        logger.info(f"用户输入: {user_input[:80]}...")
        logger.info(f"最大迭代: {max_iterations} 次")
        
        # 执行图
        final_state = self.graph.invoke(initial_state)
        
        logger.info(f"\n{'='*60}")
        logger.info(f"✅ Agent 工作流完成")
        logger.info(f"{'='*60}")
        logger.info(f"总迭代次数: {final_state['iteration']}")
        
        return final_state
