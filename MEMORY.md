# CrawAgent - 项目记忆文档

> 最后更新：2026-07-31

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

---

## 当前状态

**P0 阶段已完成 ✅**

- CrawlHarness 骨架可运行（Mock 模式）
- 真实 LLM 需要更换 API key
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

### P1 阶段（当前优先级）

1. **AntiBot 检测** - 完善反爬检测逻辑
2. **引擎 fallback 链** - 多引擎降级策略
3. **Hooks 接入** - 将 hook 骨架接入实际爬取流程
4. **更换 DeepSeek API key** - 当前 key 已失效（401）
5. **frontier.py SQLite → MySQL 迁移** - 统一数据库

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
2. **frontier.py 仍使用 SQLite** - 待迁移到 MySQL（原 P0-15，现划入 P1）
3. **工具执行器只有定义没有实际实现** - harness/tools.py 的 7 个工具只有接口，P1 阶段实现
4. **LLM 未绑定工具（tool calling 未启用）** - 需要在 loop.py 中接入 tool calling
5. **Compaction 未在 checkpoint 中自动触发** - 需要在 CrawlLoop.checkpoint 中调用
6. `crawagent/graph/site_analyzer.py` 依赖 Playwright，需要 `playwright install chromium`
7. LLM 每次生成的 CSS 选择器不一致，有时不准
8. 系统代理问题：必须通过 `main.py` 启动才能正确禁用代理
9. 多个任务共用同一个 frontier 数据库，可能互相干扰

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
