# CrawAgent Agent 能力升级设计

> 日期：2026-06-23
> 状态：已批准，待实施

## 1. 目标

参考 hermes-agent 框架，对 CrawAgent 进行三项核心能力升级：

1. **工具调用标准化** — 用 `bind_tools` 替代 JSON Prompt 解析
2. **Sub-agent 并行化** — 用 LangGraph Send API 并行处理多 URL 任务
3. **Skills 自学习系统** — Markdown Skill 文件 + 动态加载 + 自学习

## 2. 整体架构

```
┌──────────────────────────────────────────────────┐
│  TerminalUI / API                                 │
├──────────────────────────────────────────────────┤
│  AgentOrchestrator  (主调度器)                    │
├──────────────┬───────────────┬────────────────────┤
│  MainGraph   │  SubGraph ×N  │  SkillLoader      │
│  (主Agent)   │  (并行子Agent) │  (动态加载)        │
├──────────────┴───────────────┴────────────────────┤
│  LLMFactory (多Provider)                         │
├──────────────────────────────────────────────────┤
│  工具层: BaseCrawler / BrowserCrawler / ...     │
└──────────────────────────────────────────────────┘
```

## 3. 详细设计

### 3.1 工具调用标准化

**文件**：`crawagent/graph/agent_workflow.py`

**现状**：`node_think` 用 JSON Prompt 让 LLM 输出 JSON，手动解析。

**改造后**：用 `langchain-core.bind_tools()` 绑定工具，LLM 直接输出结构化 `tool_calls`。

```python
from langchain_core.tools import tool

@tool
def basic_crawler(url: str, extra_headers: dict = None) -> str:
    """基础HTTP爬虫，适合静态页面。参数：url(必需)"""
    ...

@tool
def browser_crawler(url: str, wait_seconds: int = 5) -> str:
    """浏览器自动化爬虫，适合JS渲染/反爬页面。参数：url(必需), wait_seconds(可选)"""
    ...

@tool
def packet_crawler(url: str, wait_seconds: int = 15, max_videos: int = 20) -> str:
    """网络抓包爬虫，捕获XHR/Fetch请求提取API数据。适合视频网站"""
    ...

@tool
def image_downloader(url: str, limit: int = 20, output_dir: str = None) -> str:
    """从页面提取并下载图片。参数：url(必需), limit(可选), output_dir(可选)"""
    ...

@tool
def wallpaper_scraper(url: str, max_count: int = 12, output_dir: str = None) -> str:
    """智能壁纸爬虫，自动提取高质量壁纸/视频壁纸"""
    ...

@tool
def video_metadata_scraper(url: str) -> str:
    """视频元数据爬虫，提取标题/简介/演员/流地址"""
    ...

@tool
def chat(message: str) -> str:
    """直接与用户对话，回答问题"""
    ...

# 绑定到 LLM
llm_with_tools = llm.bind_tools(
    [basic_crawler, browser_crawler, packet_crawler,
     image_downloader, wallpaper_scraper, video_metadata_scraper, chat],
    tool_choice="auto"
)
```

**节点改造**：
- `node_think` → `node_decide`：调用 `llm_with_tools`，从 `AIMessage.tool_calls` 读取
- `node_act`：根据 `tool_calls[0].name` 路由到对应 `@tool` 函数
- `node_observe` / `node_reflect` 保持不变
- 错误兜底：如果 LLM 不输出 `tool_calls`，降级到关键词匹配

### 3.2 Sub-agent 并行化

**文件**：新增 `crawagent/agent/subgraph.py`

**场景**：用户输入多个 URL（如 `"帮我爬取这三个页面：url1, url2, url3"`），或批量壁纸/视频任务。

**MainGraph 条件边**：

```python
def should_parallelize(state: AgentState) -> str:
    urls = extract_urls_from_input(state["user_input"])
    if len(urls) > 1:
        return "spawn_parallel"
    return "continue_single"

def spawn_parallel_subagents(state: AgentState):
    urls = extract_urls_from_input(state["user_input"])
    return [
        Send(url, {
            "user_input": f"爬取这个页面：{url}",
            "parent_context": state,
            "iteration": 0,
            "max_iterations": 5,
        })
        for url in urls
    ]
```

**SubGraph（子Agent）**：独立 `StateGraph`，输入单个 URL，执行完整流程，返回 `{"url", "success", "result", "error"}`。

**结果汇聚**：MainGraph 的 `node_merge_results` 收集所有子 Agent 结果，汇总成最终回复。

### 3.3 Skills 自学习系统

**文件**：新增 `crawagent/agent/skill_loader.py`

**目录结构**：

```
crawagent/
├── agent/
│   ├── skill_loader.py       # 动态加载 + 缓存
│   ├── _evaluator.py         # 任务后评估 → 是否创建新 Skill
│   └── builtins/             # 内置 Skill（只读）
│       ├── video_youku.md
│       ├── video_bilibili.md
│       ├── wallpaper_haowallpaper.md
│       └── anti_cloudflare.md
```

**Skill 文件格式**：

```markdown
# Skill: 优酷视频批量抓取

name: youku_batch_scrape
description: 适合优酷专辑页，批量提取剧集并下载
trigger_keywords: [优酷, youku, 批量下载视频, 剧集]
examples:
  - "帮我下载优酷的这个视频"
  - "爬取优酷这个专辑的所有剧集"

prompt: |
  当用户请求优酷相关任务时：
  1. 首先判断是单视频还是专辑页
  2. 单视频 → 使用 packet_crawler 抓取流地址
  3. 专辑页 → 使用 crawl_show_preview 提取所有剧集
  4. 每个视频调用 ffmpeg 转换格式
  5. 输出格式：JSON 数组含标题/URL/下载状态

tags: [视频, 优酷, 批量, 抓包]
version: 1.0
```

**加载流程**：
1. 启动时扫描 `crawagent/agent/builtins/` + `~/.crawagent/skills/`
2. 正则匹配 `trigger_keywords` → 高亮匹配的 Skill
3. 匹配到的 Skill `prompt` 内容注入 LLM System Prompt

**自学习流程**：
- 任务完成后，`_evaluator.py` 评估是否可泛化
- 若可泛化，LLM 生成新 Skill 文件存到 `~/.crawagent/skills/`

## 4. 目录结构变化

```
crawagent/
├── agent/                     # 新增
│   ├── __init__.py
│   ├── orchestrator.py        # 主调度器
│   ├── subgraph.py           # SubGraph
│   ├── skill_loader.py       # Skill 动态加载
│   ├── _evaluator.py        # 任务后评估
│   └── builtins/             # 内置 Skill
│       ├── video_youku.md
│       ├── video_bilibili.md
│       ├── wallpaper_haowallpaper.md
│       └── anti_cloudflare.md
├── graph/
│   └── agent_workflow.py     # 改造：bind_tools + Send API
└── ui/
    └── terminal.py            # 改造：/skills /skill_new 命令
```

## 5. 实施顺序

| 阶段 | 内容 | 改动范围 |
|------|------|----------|
| Phase 1 | 工具调用标准化（bind_tools） | `agent_workflow.py` |
| Phase 2 | Sub-agent 并行化 | 新增 `agent/subgraph.py` |
| Phase 3 | Skills 系统 + 内置 Skill | 新增 `agent/skill_loader.py` + builtins |
| Phase 4 | TerminalUI Skill 命令 | `ui/terminal.py` |

## 6. 依赖变化

- `langchain-core>=0.3.0` — 已有，新增 `bind_tools` API
- 无需新增依赖

## 7. 兼容性

- 保持现有 `CrawWorkflow`（旧版顺序工作流）可正常工作
- 保持现有 API 端点不变
- 渐进式改造，不破坏现有功能
