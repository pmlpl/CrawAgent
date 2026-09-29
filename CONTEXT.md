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

### 会话与产物

**工作文件夹 (work_dir)**:
会话级产物落点（变更 034）：新建对话时由用户弹系统原生对话框选定，存 meta.db `session_titles.work_dir`，随首条 WebSocket 消息 `work_dir` 字段入库。配置后该会话全部产物（文本/媒体/脚本）直接落该文件夹——不套会话子目录，媒体保留工具分类子目录（images/ 等），同时它是路径安全校验的边界（写到文件夹外即拒绝）。
_Avoid_: 与 014 的"会话产物子目录"（`output/<会话名>/`，无 work_dir 时的兜底默认）混用；与设置页全局"产物目录"混用（那是无会话粒度的根）。

**档案入库询问 (ARCHIVE-ASK)**:
抓取类任务完成时 AI MUST 用 ask_user 问「本次抓取的内容要存入档案吗？」（存入档案 / 不用 / 本会话不再询问），用户拍板后才 `save_record` 入知识库。取代旧行为"抓取成功即静默 save_record"。"本会话不再询问"由对话上下文自持，不落库。
_Avoid_: 跳过询问直接入库；对单页速览 / 失败任务 / 用户已拒绝过的会话重复弹问。
