# CrawAgent - 项目记忆文档

> 最后更新：2026-08-01

---

## 已完成的工作

### 1. 核心架构（Phase 1-5 ✅）

**后端核心模块：**
- `crawagent/core/fetcher.py` - 抓取器（httpx + curl_cffi，指数退避重试、域名限速、代理轮换、UA轮换）
- `crawagent/core/frontier.py` - 队列（SQLite + FTS5，URL去重、内容去重、断点续爬）
- `crawagent/core/extractor.py` - 提取器（CSS→XPath→LLM 三级回退，动态 Schema，详情页图片智能提取）
- `crawagent/core/retriever.py` - 检索器（SQLite FTS5 全文检索）
- `crawagent/core/executor.py` - 执行器（调度循环）
- `crawagent/core/models.py` - Pydantic 模型（含 AgentState、Selectors、SiteAnalysis 等）

**Agent 编排：**
- `crawagent/graph/agent_workflow.py` - 主图（classify→plan→analyze→execute→decide）
- `crawagent/graph/site_analyzer.py` - 站点分析子图（Playwright 抓包 + LLM 逆向）
- `crawagent/graph/anti_bot.py` - 反爬子图（策略升级链）

**基础设施：**
- `crawagent/config/settings.py` - Pydantic Settings（OpenAI 兼容接口 + MySQL/Redis/Harness 配置）
- `crawagent/llm/factory.py` - LLM 工厂（OpenAI 兼容 + Ollama + _clients 字典缓存）
- `crawagent/api/server.py` - FastAPI 服务（CORS、异步任务、配置持久化、Harness API）
- `crawagent/cli/main.py` - Click CLI

### 2. 前端界面（已完成）

**技术栈：** Vue 3 + Element Plus + Vite

**页面：**
- `/` - 首页（统计卡片、快速开始、最近任务）
- `/agent` - Agent 任务（输入指令、实时日志、结果展示、轮询状态）
- `/analysis` - 站点分析（输入 URL、选择器配置、一键爬取）
- `/results` - 爬取结果（搜索、分页、查看/删除）
- `/settings` - 设置（模型配置、爬虫配置、持久化保存）

### 3. 项目清理（ponytail audit）

删除了 AI 生成垃圾文件、未使用的模块（agent/tools/ui/workflow/landing/docs/output）

### 4. 模型配置简化

统一为 OpenAI 兼容接口：`openai_api_key` + `openai_base_url` + `default_model`

### 5. 异步任务与实时反馈

**后端：**
- `POST /api/agent/run` - 异步任务，立即返回 job_id
- `GET /api/agent/status/{job_id}` - 查询任务状态、进度、日志、结果
- 任务超时保护：5 分钟（`asyncio.wait_for`）
- 内存任务存储：`_job_store` 字典

**前端：**
- `Agent.vue` 添加轮询逻辑（每 2 秒查询一次状态）
- 实时日志展示（增量追加）
- 进度条显示

### 6. 配置持久化

**后端：**
- `GET /api/settings` - 获取当前配置
- `PUT /api/settings` - 保存配置（更新内存 + .env 文件）
- `SettingsUpdate` Pydantic 模型

**前端：**
- `Settings.vue` 页面加载时从后端拉取配置
- 保存按钮调用 PUT 接口
- 刷新页面配置不丢失

### 7. 系统代理问题修复

**问题：** Windows 系统 Privoxy 代理拦截所有 HTTP 请求

**修复位置：**
- `main.py` - 最开头设置 `os.environ["NO_PROXY"] = "*"`（必须在所有网络库导入前）
- `crawagent/core/fetcher.py` - `httpx.AsyncClient(trust_env=False)`
- `crawagent/graph/site_analyzer.py` - Playwright 启动参数 `--no-proxy-server`
- `crawagent/llm/factory.py` - 模块开头设置 `NO_PROXY` 环境变量
- `crawagent/api/server.py` - lifespan 中设置环境变量（双重保险）

### 8. 详情页图片提取增强

**修复：** `crawagent/core/extractor.py` 的 `_fallback_generic` 方法增强：
- 提取 `og:image` 元标签作为主图
- 提取页面所有 `<img>` 标签，过滤小图标（宽高 < 50px）
- 自动检测详情页 URL
- 详情页模式：返回页面标题 + 主图 URL
- 图片字段模式：每张图片一条记录

### 9. P0 阶段完成（2026-07-31）

**Bug 修复（8处）：**
- `graph/anti_bot.py`：添加 import json / 合并重复函数 handle_anti_bot_challenge → handle_anti_bot_challenge_simple / 修复 detect_challenge 返回值赋值
- `graph/site_analyzer.py`：重名函数改为 analyze_site_simple / 补全 AnalyzerState 3个字段（dom_snippet, page_title, network_summary）
- `llm/factory.py`：移除 get_llm 实例方法上的 @lru_cache，改用 _clients 字典缓存
- `core/fetcher.py`：修复 __aexit__ 方法误包含 _make_retryer 代码

**依赖 & 配置更新：**
- `pyproject.toml`：新增 aiomysql / sqlalchemy[asyncio] / redis / structlog 依赖
- `config/settings.py`：新增 MySQL 配置（mysql_host/port/user/password/database/pool_size）+ Redis 配置 + Harness 配置 + mysql_dsn 属性
- `.env`：新增 MYSQL_* 和 REDIS_URL 配置

**CrawlHarness 骨架（9个新文件）：**
- `harness/types.py`：11个核心类型（Phase, HookEvent, CrawlMessage, TurnSnapshot, RunResult, OperationType, OperationRecord, LaneInfo, ToolExecMode, CrawlToolDef, TokenUsage）
- `harness/session.py`：CrawlSession + SessionManager + 5个 ORM 模型（MySQL 持久化）
- `harness/hooks.py`：CrawlHooks（8种事件拦截）+ 3个内置 hook 骨架
- `harness/loop.py`：CrawlLoop（driverLoop：checkpoint → step → tools → finish）
- `harness/harness.py`：CrawlHarness 主类（session + hooks + lanes 编排层）+ quick_crawl
- `harness/tools.py`：7个内置工具（crawl/extract/analyze/save/search/monitor/scan_vuln）+ ToolRegistry
- `harness/env.py`：CrawlEnv（文件系统/HTTP/浏览器环境抽象）
- `harness/compaction.py`：Compaction（上下文压缩）
- `harness/system_prompt.py`：SystemPromptAssembler（四段式提示词组装）

**数据库 & 部署：**
- `core/database.py`：DatabaseManager + Base（SQLAlchemy 2.0 async MySQL）
- `docker-compose.yml`：MySQL 8.0 + Redis 7 容器
- `docker/mysql/init.sql`：初始化脚本

**API 接入：**
- `api/server.py`：新增 Harness API 端点（/api/harness/sessions, /api/harness/prompt, /api/harness/quick-crawl, lanes, stop）+ 数据库初始化

**运行时 Bug 修复：**
- `harness/types.py`：RunResult.error 类方法与字段同名冲突 → 改为 from_error
- `harness/loop.py`：_step() LLM 调用失败添加错误日志（原静默吞异常）

**验证通过：**
- Python 导入全部成功
- MySQL 连接 + 建表 + Session 创建正常
- Agent Loop Mock 模式端到端跑通
- FastAPI 启动 + Harness API 端点测试通过
- DeepSeek API key 失效（401），需更换

### 10. P1 收尾闭环完成（2026-08-01）

**7 项关键修复（提交 8e16029）：**
- 对话消息顺序反转：`session.get_entries` 默认按时间正序
- `loop._step` 接入 SystemPromptAssembler（系统提示词真正发给 LLM）
- `RunResult.error` → `RunResult.from_error`（异常路径崩溃修复）
- AntiBot hooks 接入 supervisor：`before_tool` 注入 use_browser，`after_tool` 从 `stages.crawl.status` 提取真实 HTTP 状态
- lane leaf 指针随消息追加更新（对话树不再是扁平列表）
- Compaction 在 checkpoint 触发
- API 修复：quick-crawl 返回真实 session_id、统一 instruction 字段、新增 DELETE /sessions/{id}

**时间精度修复（提交 b964140）：**
- harness 表时间列 Float(单精度) → Double（Unix 时间戳丢亚秒精度导致同秒排序不稳）
- 新增 `docker/mysql/migrate_v2_time_precision.sql` 迁移脚本

### 11. P2 阶段完成（2026-08-01，代码级）

**内容清洗 + LLM 提取 + 提示词工程：**
- `core/content_filter.py`：三档过滤器（Pruning 规则去噪 / BM25 相关性过滤（自实现）/ LLM 语义过滤）
- `core/markdown_generator.py`：html2text 定制转换 + citation 引用格式 + fit_markdown 截断
- `core/chunking.py`：7 种分块策略（identity/regex/sentence/fixed/sliding/overlapping/recursive）
- `core/extractor.py`：策略模式 8 种提取器（CSS/XPath/LLM/Regex/JSON-LD/Meta/Table/JsonCss）
- `llm/prompts.py`：6 个高质量提示词 + JSON_SCHEMA_BUILDER（保留旧流水线提示词兼容）
- `llm/usage.py`：TokenUsage 跟踪（响应元数据自动采集 + LangChain callback）

**管线接入：**
- Supervisor 增加 clean 阶段：crawl → clean（去噪 + Markdown）→ extract → save
- 测试 37 个全部通过（含脏数据：空 body / 纯 script / 1MB HTML 清洗不崩）

### 12. P2 验证与修复（2026-08-01）

**实机验证（苹果官网 https://www.apple.com/shop/buy-iphone）：**
- httpx 直连 200，无需回退 Playwright ✓
- Markdown 无 `</path>` 类残留 ✓
- Markdown 无 "html" 前缀 ✓

**验证中发现并修复的 bug：**
- `content_filter.py` 负模式 `ads?` 无词边界，误伤 `<body class="dd-apple-upgrade-...">`
  （"upgrade" 含 "ad"），导致 body 被整体删除、Markdown 只剩 "html" 前缀。
  已改为词边界匹配 `\b(ads?)\b`，并显式跳过 html/body 骨架节点。
- `fetcher.py`：懒初始化 httpx/curl_cffi 客户端（未走 `async with` 时不再静默回退 Playwright）
- `markdown_generator.py`：html2text 前预处理相对链接 → 绝对 URL + 提取 body（修复 baseurl 拼接 bug）

**回归测试：40 个全部通过**（新增 3 个：body class 含 "ad" 不被误删、相对链接绝对化、无 html 前缀）

---

## 当前状态

**P0 + P1 完成 ✅，P2 完成并通过苹果官网/HN 验证 ✅（知乎待网络环境）**

- P0：CrawlHarness 骨架可运行（Mock 模式）
- P1：引擎回退链 + AntiBot hooks + 时间精度修复完成
- P2：清洗/提取/分块/提示词/用量模块已实现，40 个测试全过，苹果官网实机验证通过
- 真实 LLM 需要更换 API key（当前 DeepSeek key 已失效）
- 服务运行在 http://localhost:8000

**可运行的命令：**
```bash
# 后端
python main.py check          # 环境检查
python main.py serve           # 启动 API 服务 (localhost:8000)
python main.py run "指令"      # CLI 运行 Agent

# 前端
cd frontend && npm run dev     # 启动前端 (localhost:5173)

# Docker（MySQL + Redis）
docker-compose up -d           # 启动数据库容器
```

**配置：** `.env` 已配 DeepSeek API（sk-370af...，已失效），base_url: api.deepseek.com/v1

**重要：** 必须通过 `main.py` 启动服务，才能正确禁用系统代理。

---

## 下一步

### P2 验收（当前优先级）

1. **知乎验收** - 换有效 API key 后抓取知乎问题页确认 Markdown 干净（本机网络无法连接 zhihu/HN）
2. **LLM 过滤器验证** - LLMContentFilter 用真实 key 跑通（PROMPT_FILTER_CONTENT）

### 中优先级

3. **P3 文件整理** - output/organizer + media_downloader + save 工具增强（下一步）
4. **WebSocket 支持** - Agent 执行时实时推送日志到前端
5. **任务持久化** - 当前任务结果存在内存，需要持久化到 MySQL
6. **frontier.py SQLite → MySQL 迁移** - 统一数据库
7. **工具执行器补充** - monitor / scan_vuln 执行器

### 中优先级

6. **WebSocket 支持** - Agent 执行时实时推送日志到前端（替代轮询）
7. **任务持久化** - 当前任务结果存在内存，需要持久化到 MySQL
8. **错误处理** - 前端统一错误提示
9. **任务队列隔离** - 当前所有任务共用一个 frontier 数据库，需要按 job_id 隔离
10. **工具执行器实现** - harness/tools.py 只有定义，P1 阶段实现实际逻辑
11. **LLM tool calling 启用** - 当前 LLM 未绑定工具

### 低优先级

12. **Phase 6** - 登录/Cookie 注入
13. **Phase 7** - 签名接口逆向
14. **Phase 8** - 验证码处理

---

## 已知问题

1. **DeepSeek API key 失效（401）** - 需更换有效 key
2. **frontier.py 仍使用 SQLite** - 待迁移到 MySQL
3. **monitor / scan_vuln 执行器仍为空** - 对应 P4/P5 阶段
4. **LLMContentFilter 未用真实 key 验证** - 单测只覆盖非 LLM 路径
5. `crawagent/graph/site_analyzer.py` 依赖 Playwright，需要 `playwright install chromium`
6. LLM 每次生成的 CSS 选择器不一致，有时不准
7. 系统代理问题：必须通过 `main.py` 启动才能正确禁用代理
8. 多个任务共用同一个 frontier 数据库，可能互相干扰

---

## 文件清单（核心）

```
CrawAgent/
├── main.py                    # CLI 入口（含 NO_PROXY 设置，必须用此启动）
├── pyproject.toml             # 依赖（含 aiomysql/sqlalchemy/redis/structlog）
├── docker-compose.yml         # MySQL 8.0 + Redis 7 容器
├── .env.example               # 环境变量模板
├── .env                       # 实际配置（DeepSeek API + MySQL + Redis）
├── README.md
├── ARCHITECTURE.md
├── MEMORY.md                  # 本文档
├── docker/
│   └── mysql/
│       └── init.sql           # MySQL 初始化脚本
├── crawagent/
│   ├── __init__.py
│   ├── config/
│   │   └── settings.py        # Pydantic Settings（MySQL/Redis/Harness 配置 + mysql_dsn）
│   ├── core/
│   │   ├── fetcher.py         # httpx (trust_env=False)
│   │   ├── frontier.py        # SQLite 队列（待迁移 MySQL）
│   │   ├── extractor.py       # 三级回退 + 图片提取增强
│   │   ├── retriever.py
│   │   ├── models.py
│   │   ├── executor.py
│   │   ├── database.py        # DatabaseManager + Base（SQLAlchemy 2.0 async MySQL）
│   │   └── job_store.py
│   ├── graph/
│   │   ├── agent_workflow.py  # 主工作流
│   │   ├── site_analyzer.py   # Playwright (--no-proxy-server) + analyze_site_simple
│   │   ├── anti_bot.py        # 反爬（handle_anti_bot_challenge_simple）
│   │   └── prompts/
│   │       └── site_analyzer.py
│   ├── harness/
│   │   ├── __init__.py
│   │   ├── types.py           # 11个核心类型（Phase/HookEvent/CrawlMessage/RunResult...）
│   │   ├── session.py         # CrawlSession + SessionManager + 5个 ORM 模型
│   │   ├── hooks.py           # CrawlHooks（8种事件拦截）+ 3个内置 hook
│   │   ├── loop.py            # CrawlLoop（driverLoop：checkpoint→step→tools→finish）
│   │   ├── harness.py         # CrawlHarness 主类 + quick_crawl
│   │   ├── tools.py           # 7个内置工具 + ToolRegistry
│   │   ├── env.py             # CrawlEnv（文件系统/HTTP/浏览器环境抽象）
│   │   ├── compaction.py      # Compaction（上下文压缩）
│   │   └── system_prompt.py   # SystemPromptAssembler（四段式提示词组装）
│   ├── llm/
│   │   ├── factory.py         # NO_PROXY 设置 + _clients 字典缓存
│   │   ├── mock.py            # Mock LLM（测试用）
│   │   └── prompts.py
│   ├── api/
│   │   └── server.py          # FastAPI（异步任务+配置持久化+Harness API）
│   └── cli/
│       └── main.py
├── data/
│   ├── crawl_frontier.db
│   └── jobs.db
└── frontend/
    ├── package.json
    ├── vite.config.js
    └── src/
        ├── main.js
        ├── App.vue
        ├── api/index.js
        ├── router/index.js
        └── views/
            ├── Agent.vue      # 轮询 + 实时日志
            ├── Analysis.vue
            ├── Home.vue
            ├── Results.vue
            └── Settings.vue   # 配置持久化
```
