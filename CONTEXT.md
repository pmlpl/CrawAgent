# CrawAgent

LLM 驱动的智能爬虫 Agent 框架。单一上下文：本文件是全项目唯一词汇表，术语定稿即落此处。

## Language

### 抓取过程

**翻页 (Pagination)**:
在同一个列表页的第 1→N 页之间翻动，把列表条目收齐。纯机械工作，可通用化。
_Avoid_: 深度爬取、批量抓取（三者互指会掩盖边界）

**深度爬取 (Deep Crawl)**:
从列表条目跳进各详情页做二次提取。详情页结构千站千面，属语义工作。
_Avoid_: 翻页、递归爬取

**批量抓取 (Batch Crawl)**:
把多页/多条目的整个工作负载合并进一次脚本调用内循环完成（清单自建、翻页自翻、去重、节流、失败汇总）。
_Avoid_: 逐条调用（明确的反模式，上下文会爆）

### 生态扩展

**MCP server**:
CrawAgent 通过 MCP 协议接入的外部能力单元。一条配置 = name + transport（streamable_http / stdio）+ url 或 command+args + 鉴权 headers，存在 `.env` 的 `MCP_SERVERS`（JSON 数组）。构建期由 `build_mcp_tools` 连接并装入原生工具；可 `disabled` 停用（配置保留但不装工具）。
_Avoid_: 把它和"工具"混用——工具是 `@tool` 函数，MCP server 是工具的来源之一。

**CrawAgent skill**:
一个目录里的 `SKILL.md`（带 YAML frontmatter），从 `skills_dirs` + 插件 `skills/` 扫描，构建期注入 system prompt 索引，`read_skill(name)` 读取。是 Agent 的"可安装专长模块"。
_Avoid_: 与 **ZCode 工作流技能**（grill/implement/tdd，在 `~/.agents/`，给 agent 宿主用）和 **runtime skills 模块**（`crawagent/graph/skills.py`，Python 代码）混称——三者都叫"skill"但不是一回事。

**插件 (Plugin)**:
`plugins/` 下带 `plugin.json` manifest 的目录，可同时携带 `tools/*.py`（自动扫描的 @tool）和 `skills/`（CrawAgent skill）。靠文件系统发现，启动期扫描。
_Avoid_: 与 MCP server 混——插件是本地代码包，MCP server 是协议端点。
