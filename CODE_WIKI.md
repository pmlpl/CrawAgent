# CrawAgent Code Wiki

> 版本：v1.1 | 生成日期：2026-08-02
> 本文档为 CrawAgent 代码库的结构化索引，面向开发者快速理解架构、定位模块与关键符号。
> v1.1 更新：新增网站画像系统（core/site_profile.py）、图片专用提取器（extract_images）、SPA 客户端路由处理、Playwright 滚动触发懒加载、LLM 工具调用完整性自愈、Supervisor 图片意图识别

---

## 1. 项目概览

### 1.1 项目定位

CrawAgent 是一套**基于 pi Agent Harness 架构**的生产级智能爬虫框架。核心理念是「真正的 Agent，而非固定流水线」——LLM 在 loop 中自主选择工具，hooks 拦截/修改行为，lanes 并行处理多任务。

- **入口**：给任意 URL → LLM 自主决策 → 工具调用 → 结构化落库
- **用途**：个人学习/研究，不分发不售卖
- **技术栈**：Python 3.11+ / FastAPI / Vue 3 / MySQL 8.0 / Redis 7 / Playwright

### 1.2 核心能力

| 能力 | 说明 |
|------|------|
| Agent Harness | LLM 自主决策 loop + 8 种 hooks + lanes 并行 |
| 三引擎 fallback | httpx → curl_cffi → Playwright，自动升级 |
| 反爬检测 | 三层检测（状态码+响应头+响应体），7 种挑战类型 |
| 深度爬取 | BFS/DFS/Best-First + URL 过滤器链 + 饱和度感知 |
| 文件整理 | 路径模板引擎，按 `domain/date/title.md` 自动落盘 |
| 监控场景 | 定时爬取 + diff 检测 + Webhook/飞书告警 |
| 安全扫描 | OWASP Top 10 漏洞扫描 + 自动修复补丁 |
| 视频下载 | yt-dlp 集成 + 播放列表下载 + 本地播放器 |
| 上下文压缩 | 长任务 token 超限自动压缩 + 崩溃恢复 |
| 登录态持久化 | Playwright persistent_context，跨会话复用 cookie |
| 代理轮换 | HTTP/SOCKS5 代理池 + 健康检查 + 冷却恢复 |
| 图片专用提取 | img 懒加载/meta og/JSON-LD/背景图多源提取 + 去重分类（v1.1） |
| SPA 客户端路由 | Nuxt/Vue/React/Next 4xx 自动等待路由渲染并改写 200（v1.1） |
| 懒加载触发 | Playwright 滚动到底部，触发 data-src/data-original 加载（v1.1） |
| 网站画像系统 | 自动发现技术栈/图片加载/反爬等级/导航结构，支持针对性优化（v1.1） |
| 工具调用完整性 | tool_calls/tool_response 配对自愈，避免 OpenAI 400 错误（v1.1） |

### 1.3 开发阶段路线

| 阶段 | 目标 | 状态 |
|------|------|------|
| P0 | MySQL 迁移 + Harness 骨架 | ✅ |
| P1 | 反爬检测 + 引擎 fallback 链 | ✅ |
| P2 | 内容清洗 + LLM 提取 | ✅ |
| P3 | 文件整理到指定路径 | ✅ |
| P4 | 监控场景 + Lanes | ✅ |
| P5 | vibe coding 安全检查 | ✅ |
| P6 | Compaction + Durability + 深度爬取 | ✅ |
| P7 | 视频下载 + 广告移除 | ✅ |
| P2+ | 图片专用提取 + SPA 路由 + 网站画像 + LLM 完整性修复 | ✅ |
| P8 | Docker 部署 | 本地可选 |

---

## 2. 整体架构

CrawAgent 采用分层架构，自上而下依次为用户层、API 网关层、Agent Harness 编排层、工具执行层、存储/基础设施层。

```
┌─────────────────────────────────────────────────────────────────────────┐
│                            用户层                                       │
│   CLI (Click)  /  Vue 3 Web UI  /  API Client                          │
└────────────────────────────────┬────────────────────────────────────────┘
                                 │
                                 ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                          API 网关层 (FastAPI)                            │
│   /api/harness/*  /api/agent/*  /api/crawl/*  /api/monitor/*           │
│   /api/security/*  /api/video/*  /api/output/*  /api/settings         │
└────────────────────────────────┬────────────────────────────────────────┘
                                 │
                                 ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                    Agent Harness 编排层 (harness/)                       │
│   CrawlHarness (session+hooks+lanes+tools)                             │
│   CrawlLoop (driverLoop: checkpoint→step→execute_tool_batch→followUp)  │
│   CrawlSession (MySQL 持久化) / CrawlHooks / CrawlEnv / Compaction     │
│   SystemPromptAssembler (四段式)                                        │
└────────────────────────────────┬────────────────────────────────────────┘
                                 │ Tool Call
                                 ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                         工具执行层                                       │
│   内置工具：supervisor(编排入口)/search/monitor/check_change/          │
│            scan_vuln/fix_issue                                          │
│   引擎层 (engines/)：FallbackChain → httpx → curl_cffi → Playwright     │
│   提取器 (extractors/)：VideoExtractor / AdRemover                      │
└────────────────────────────────┬────────────────────────────────────────┘
                                 │
                                 ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                      存储 / 基础设施层                                   │
│   MySQL (entries/operation_logs/global_facts) + Redis (缓存/限速)       │
│   SQLite (JobStore/MonitorStore/SecurityStore/Frontier)                 │
│   文件系统 (爬取结果/视频下载) + 代理池 + 登录态持久化                    │
└─────────────────────────────────────────────────────────────────────────┘
```

### 2.1 设计哲学（继承自 pi Agent Harness）

| 模式 | 说明 |
|------|------|
| Turn Snapshot 不可变 | 每次 LLM step 产生的 turn 记录一旦写入即不可修改，只能追加 |
| Save Point 刷新 | 每轮循环结束后刷新保存点，崩溃恢复从最近保存点开始 |
| Hooks 先于效果 | hook 回调在副作用发生前执行，可拦截/修改/中止即将发生的操作 |
| Append-only 树 | 对话树只增不改不删，天然支持回溯、审计与分支 |
| Lanes 并行 | 同一会话内通过 lanes 支持多并行分支（main/monitor/security） |
| Durability | 操作执行前先写 operation_logs 并预分配 ID，at-least-once 语义 |
| 结果而非异常 | 所有执行结果通过 `RunResult` 判别联合返回，4 种 kind 覆盖全部状态 |
| Compaction 内建 | 上下文压缩作为一等公民，token 超限自动触发 |

---

## 3. 目录结构

```
CrawAgent/
├── crawagent/                  # 后端主包
│   ├── harness/                # pi Agent Harness 核心（编排层）
│   ├── engines/                # 引擎抽象层（三引擎 fallback）
│   ├── extractors/             # 提取器（视频/广告）
│   ├── core/                   # 核心组件（fetcher/extractor/site_profile/deep_crawl 等）
│   ├── security/               # 安全扫描（P5）
│   ├── monitor/               # 监控模块（P4）
│   ├── output/                 # 文件整理 + 媒体下载（P3/P7）
│   ├── sessions/               # 登录态持久化
│   ├── js_snippets/            # JS 注入片段
│   ├── graph/                  # 旧 Agent 编排（兼容保留）
│   ├── llm/                    # LLM 工厂与提示词
│   ├── api/server.py           # FastAPI 服务
│   ├── cli/main.py             # Click CLI
│   └── config/settings.py      # Pydantic Settings
├── frontend/                   # Vue 3 + Vite 前端
│   └── src/{views,api,router,styles}
├── docker/                     # MySQL 初始化脚本
├── tests/                      # pytest 测试套件
├── main.py                     # CLI 入口
├── pyproject.toml              # Python 依赖与构建配置
├── docker-compose.yml          # MySQL + Redis
├── ARCHITECTURE.md             # 详细架构设计
├── DEVELOPMENT_PLAN.md         # 开发路线图
├── KNOWN_GAPS.md               # 已知缺口清单
└── README.md
```

---

## 4. 核心模块详解

### 4.1 harness/ — Agent Harness 编排层

pi Agent Harness 的 Python 实现，是整个系统的核心编排层。

| 文件 | 职责 | 关键类/函数 |
|------|------|-------------|
| [harness.py](file:///c:/Users/MOM/Desktop/学习项目/CrawAgent/crawagent/harness/harness.py) | 编排层主类，管理 session/hooks/lanes/tools 生命周期 | `CrawlHarness`（`prompt()` / `quick_crawl()` 入口） |
| [loop.py](file:///c:/Users/MOM/Desktop/学习项目/CrawAgent/crawagent/harness/loop.py) | driverLoop 核心循环 | `CrawlLoop`（`run()` / `_checkpoint()` / `_step()` / `_execute_tool_batch()`） |
| [session.py](file:///c:/Users/MOM/Desktop/学习项目/CrawAgent/crawagent/harness/session.py) | MySQL 持久化会话 + 崩溃恢复 | `CrawlSession`、`SessionManager`、`IdPool` |
| [hooks.py](file:///c:/Users/MOM/Desktop/学习项目/CrawAgent/crawagent/harness/hooks.py) | 8 种 hook 事件注册与链式执行 | `CrawlHooks`（`on`/`off`/`fire`）、`AntiBotHookHandler` |
| [tools.py](file:///c:/Users/MOM/Desktop/学习项目/CrawAgent/crawagent/harness/tools.py) | 工具定义 + 执行器 + Supervisor 调度 | `ToolRegistry`、`CrawlSupervisor`、`create_default_tools()` |
| [compaction.py](file:///c:/Users/MOM/Desktop/学习项目/CrawAgent/crawagent/harness/compaction.py) | 上下文压缩 + 上下文重建 | `Compaction`（`should_compact` / `compact` / `rebuild_context`） |
| [types.py](file:///c:/Users/MOM/Desktop/学习项目/CrawAgent/crawagent/harness/types.py) | 核心类型定义 | `Phase`、`HookEvent`、`CrawlMessage`、`TurnSnapshot`、`RunResult`、`CrawlToolDef`、`RecoveryPlan` |
| [env.py](file:///c:/Users/MOM/Desktop/学习项目/CrawAgent/crawagent/harness/env.py) | 执行环境抽象（文件/HTTP/浏览器资源管理） | `CrawlEnv` |
| [system_prompt.py](file:///c:/Users/MOM/Desktop/学习项目/CrawAgent/crawagent/harness/system_prompt.py) | 四段式系统提示词组装 | `SystemPromptAssembler`（core/site_feature/task_notes/user_append） |
| [antibot_hook.py](file:///c:/Users/MOM/Desktop/学习项目/CrawAgent/crawagent/harness/antibot_hook.py) | before_tool 反爬拦截（注入升级策略） | 反爬挑战状态注入 |
| [escalation_hook.py](file:///c:/Users/MOM/Desktop/学习项目/CrawAgent/crawagent/harness/escalation_hook.py) | after_response 自动引擎升级 | `AddFeatureError` |

**8 种 Hook 事件**：

| 事件 | 触发时机 | 典型用途 |
|------|----------|----------|
| `before_run` | 每轮循环开始前 | 注入上下文、条件终止 |
| `before_tool` | 工具执行前 | 参数改写、权限校验、反爬注入 |
| `after_tool` | 工具执行后 | 结果改写、反爬检测、日志记录 |
| `transform_context` | 上下文组装后、发送 LLM 前 | 上下文裁剪、注入指令 |
| `before_request` | LLM 请求发送前 | 请求改写、限流 |
| `after_response` | LLM 响应返回后 | 响应改写、引擎升级 |
| `before_compaction` / `after_compaction` | 上下文压缩前/后 | 自定义压缩策略 |
| `before_run_end` | 循环结束前 | 结果汇总、资源清理 |

### 4.2 engines/ — 引擎抽象层

统一的 HTTP 抓取引擎接口，由 [FallbackChain](file:///c:/Users/MOM/Desktop/学习项目/CrawAgent/crawagent/engines/fallback.py) 按 waterfall 依次尝试。

| 文件 | 引擎 | 特性 |
|------|------|------|
| [base.py](file:///c:/Users/MOM/Desktop/学习项目/CrawAgent/crawagent/engines/base.py) | `BaseEngine` 抽象基类 + `EngineConfig` | 定义 `name`/`is_available`/`fetch`/`close` 接口 |
| [httpx_engine.py](file:///c:/Users/MOM/Desktop/学习项目/CrawAgent/crawagent/engines/httpx_engine.py) | `HttpxEngine` | 最快，无 JS 渲染，第一优先级 |
| [curl_cffi_engine.py](file:///c:/Users/MOM/Desktop/学习项目/CrawAgent/crawagent/engines/curl_cffi_engine.py) | `CurlCffiEngine` | TLS 指纹模拟（`impersonate=chrome120`），绕过 Cloudflare |
| [playwright_engine.py](file:///c:/Users/MOM/Desktop/学习项目/CrawAgent/crawagent/engines/playwright_engine.py) | `PlaywrightEngine` | 完整浏览器渲染 + JS 注入 + 登录态，最慢但最强 |
| [fallback.py](file:///c:/Users/MOM/Desktop/学习项目/CrawAgent/crawagent/engines/fallback.py) | `FallbackChain` | waterfall 执行器，结合 `is_blocked()` 检测自动升级 |

默认优先级：`httpx → curl_cffi → Playwright`（由快到慢）。引擎不负责重试、限速、代理轮换——这些由上层 [Fetcher](file:///c:/Users/MOM/Desktop/学习项目/CrawAgent/crawagent/core/fetcher.py) / FallbackChain 管理。

### 4.3 core/ — 核心组件

| 文件 | 职责 | 关键类/函数 |
|------|------|-------------|
| [models.py](file:///c:/Users/MOM/Desktop/学习项目/CrawAgent/crawagent/core/models.py) | Pydantic 模型与 LangGraph State | `CrawlResult`、`ExtractedItem`、`Selectors`、`SiteAnalysis`、`CrawlPlan`、`CrawlJob`、`AgentState` |
| [database.py](file:///c:/Users/MOM/Desktop/学习项目/CrawAgent/crawagent/core/database.py) | SQLAlchemy 2.0 async 数据库管理 | `Base`、`DatabaseManager`、`get_db()` |
| [fetcher.py](file:///c:/Users/MOM/Desktop/学习项目/CrawAgent/crawagent/core/fetcher.py) | 双引擎抓取 + 限速 + 代理轮换 + SPA 客户端路由 + 滚动触发懒加载 | `Fetcher`、`DomainLimiter`（令牌桶）、`ProxyRotator` |
| [extractor.py](file:///c:/Users/MOM/Desktop/学习项目/CrawAgent/crawagent/core/extractor.py) | 三级回退提取（8 种提取器）+ 图片专用提取 | `CompositeExtractor`、`SelectolaxExtractor`、`LxmlExtractor`、`LLMExtractor`、`RegexExtractor`、`JsonLdExtractor`、`MetaExtractor`、`TableExtractor`、`JsonCssExtractor`（`extract_images()` 图片多源提取） |
| [filters.py](file:///c:/Users/MOM/Desktop/学习项目/CrawAgent/crawagent/core/filters.py) | URL 过滤器链 | `URLFilter`（基类）、`SameDomainFilter`、`MaxDepthFilter`、`ExtensionFilter`、`FilterChain` |
| [deep_crawl.py](file:///c:/Users/MOM/Desktop/学习项目/CrawAgent/crawagent/core/deep_crawl.py) | BFS/DFS/Best-First 深度爬取 | `DeepCrawler`、`DeepCrawlStrategy`、`default_url_score()` |
| [adaptive.py](file:///c:/Users/MOM/Desktop/学习项目/CrawAgent/crawagent/core/adaptive.py) | 饱和度感知爬取 | `AdaptiveCrawler`、`Topic`、`Saturation`、`Throttler` |
| [frontier.py](file:///c:/Users/MOM/Desktop/学习项目/CrawAgent/crawagent/core/frontier.py) | URL 队列（优先级+去重） | `SQLiteFrontier`、`canonicalize_url()`、`url_fingerprint()` |
| [retriever.py](file:///c:/Users/MOM/Desktop/学习项目/CrawAgent/crawagent/core/retriever.py) | 全文检索（SQLite FTS5） | `SQLiteFTS5Retriever`、`create_retriever` |
| [executor.py](file:///c:/Users/MOM/Desktop/学习项目/CrawAgent/crawagent/core/executor.py) | 爬取执行器 | `CrawlExecutor` |
| [proxy.py](file:///c:/Users/MOM/Desktop/学习项目/CrawAgent/crawagent/core/proxy.py) | 代理配置 + 轮换代理池 | `ProxyConfig`、`RoundRobinProxy`、`ProxyHealth` |
| [errors.py](file:///c:/Users/MOM/Desktop/学习项目/CrawAgent/crawagent/core/errors.py) | 错误分类与重试判定 | `ErrorCategory`、`classify_error()`、`should_retry()` |
| [content_filter.py](file:///c:/Users/MOM/Desktop/学习项目/CrawAgent/crawagent/core/content_filter.py) | 内容清洗（P2） | `PruningContentFilter`、`BM25ContentFilter`、`LLMContentFilter` |
| [markdown_generator.py](file:///c:/Users/MOM/Desktop/学习项目/CrawAgent/crawagent/core/markdown_generator.py) | HTML→Markdown | `MarkdownGenerator`、`html_to_clean_markdown()` |
| [chunking.py](file:///c:/Users/MOM/Desktop/学习项目/CrawAgent/crawagent/core/chunking.py) | 分块策略 | `RecursiveChunking`、`SlidingWindowChunking`、`FixedSizeChunking` 等 7 种 |
| [job_store.py](file:///c:/Users/MOM/Desktop/学习项目/CrawAgent/crawagent/core/job_store.py) | 任务持久化 | `get_job_store()` |
| [site_profile.py](file:///c:/Users/MOM/Desktop/学习项目/CrawAgent/crawagent/core/site_profile.py) | 网站画像系统（v1.1） | `SiteProfile`（`discover()` 自动发现）、`SiteProfileStore`（CRUD+搜索）、`get_profile_store()` 全局单例 |

### 4.4 extractors/ — 提取器模块

| 文件 | 职责 | 关键类 |
|------|------|--------|
| [video_extractor.py](file:///c:/Users/MOM/Desktop/学习项目/CrawAgent/crawagent/extractors/video_extractor.py) | 视频 URL 提取（`<video>`/`<source>`/iframe/m3u8/mp4） | `VideoExtractor` |
| [ad_remover.py](file:///c:/Users/MOM/Desktop/学习项目/CrawAgent/crawagent/extractors/ad_remover.py) | DOM 广告移除（CSS 选择器+脚本域名+空容器清理） | `AdRemover` |

### 4.5 output/ — 文件整理与媒体下载

| 文件 | 职责 | 关键类/函数 |
|------|------|-------------|
| [organizer.py](file:///c:/Users/MOM/Desktop/学习项目/CrawAgent/crawagent/output/organizer.py) | 路径模板引擎 + 非法字符转义 | `FileOrganizer`、`sanitize_filename()`、`title_to_slug()`、`unique_path()`、`organize_content()` |
| [media_downloader.py](file:///c:/Users/MOM/Desktop/学习项目/CrawAgent/crawagent/output/media_downloader.py) | yt-dlp + httpx 流式下载（含播放列表） | `MediaDownloader`、`ydl_prepare_filename()` |

路径模板支持占位符：`{domain}`/`{date}`/`{year}`/`{month}`/`{day}`/`{title}`/`{slug}`/`{ext}`，默认模板 `articles/{domain}/{date}/{title}.{ext}`。

### 4.6 monitor/ — 监控模块（P4）

定时爬取 + 变化检测 + 告警。存储用 SQLite，调度器基于 APScheduler（缺包时降级为手动触发）。告警去抖：cooldown 间隔内同 URL 只告警一次。

| 文件 | 职责 | 关键类 |
|------|------|--------|
| [models.py](file:///c:/Users/MOM/Desktop/学习项目/CrawAgent/crawagent/monitor/models.py) | 监控任务/告警 Pydantic 模型 + SQLite 存储 | `MonitorTask`、`AlertRecord`、`MonitorStore`、`ScheduleType`、`AlertLevel` |
| [baseline.py](file:///c:/Users/MOM/Desktop/学习项目/CrawAgent/crawagent/monitor/baseline.py) | 基线快照存储 | `BaselineStore` |
| [diff_detector.py](file:///c:/Users/MOM/Desktop/学习项目/CrawAgent/crawagent/monitor/diff_detector.py) | 变化检测（内容 hash / 字段对比 / 结构化 diff） | `DiffDetector`、`DiffResult` |
| [notifier.py](file:///c:/Users/MOM/Desktop/学习项目/CrawAgent/crawagent/monitor/notifier.py) | 告警通知 | `Notifier`（Webhook / 飞书 / 邮件） |
| [scheduler.py](file:///c:/Users/MOM/Desktop/学习项目/CrawAgent/crawagent/monitor/scheduler.py) | APScheduler 调度（cron / interval） | `MonitorScheduler` |
| [monitor_lane.py](file:///c:/Users/MOM/Desktop/学习项目/CrawAgent/crawagent/monitor/monitor_lane.py) | harness 的 monitor lane 集成 | `MonitorLane` |

### 4.7 security/ — 安全扫描模块（P5）

OWASP Top 10 漏洞扫描 + 自动修复补丁生成。扫描器是无副作用的只读检查，注入测试使用受控 payload + 回显检测，不破坏目标。

| 文件 | 职责 | 关键类 |
|------|------|--------|
| [models.py](file:///c:/Users/MOM/Desktop/学习项目/CrawAgent/crawagent/security/models.py) | 漏洞/扫描任务/补丁模型 + SQLite 存储 | `Vulnerability`、`ScanTask`、`ScanResult`、`Severity`、`VulnCategory`、`SecurityStore` |
| [vuln_scanner.py](file:///c:/Users/MOM/Desktop/学习项目/CrawAgent/crawagent/security/vuln_scanner.py) | OWASP Top 10 扫描引擎 | `VulnScanner` |
| [auto_fixer.py](file:///c:/Users/MOM/Desktop/学习项目/CrawAgent/crawagent/security/auto_fixer.py) | 生成 diff + 补丁文件 | `AutoFixer`、`FixPatch` |
| [network_capture.py](file:///c:/Users/MOM/Desktop/学习项目/CrawAgent/crawagent/security/network_capture.py) | 浏览器网络请求捕获 | `NetworkCapture` |
| [security_hook.py](file:///c:/Users/MOM/Desktop/学习项目/CrawAgent/crawagent/security/security_hook.py) | harness security hook（拦截危险操作 + 异常检测） | `SecurityHookHandler`、`create_security_hooks()` |
| [security_lane.py](file:///c:/Users/MOM/Desktop/学习项目/CrawAgent/crawagent/security/security_lane.py) | harness security lane 编排 | `SecurityLane` |

> 安全扫描的具体检测特征与 payload 模式属于实现细节，本文档不展开，详见模块源码注释。

### 4.8 llm/ — LLM 工厂与提示词

| 文件 | 职责 | 关键类/函数 |
|------|------|-------------|
| [factory.py](file:///c:/Users/MOM/Desktop/学习项目/CrawAgent/crawagent/llm/factory.py) | 多提供商 LLM 工厂（OpenAI 兼容 / Ollama / Mock） | `LLMFactory`、`LLMProvider`、`get_llm()`、`get_llm_with_tools()` |
| [mock.py](file:///c:/Users/MOM/Desktop/学习项目/CrawAgent/crawagent/llm/mock.py) | Mock LLM（测试用） | `MockLLM` |
| [prompts.py](file:///c:/Users/MOM/Desktop/学习项目/CrawAgent/crawagent/llm/prompts.py) | 提示词模板 | — |
| [usage.py](file:///c:/Users/MOM/Desktop/学习项目/CrawAgent/crawagent/llm/usage.py) | Token 使用统计 | `TokenUsage` |

LLM 客户端带缓存（按 `provider:model:temperature` 键），绑定工具时返回新的 `RunnableBinding` 不污染缓存。Mock 模式优先，便于无 API key 时测试。

### 4.9 其他模块

| 模块 | 文件 | 职责 |
|------|------|------|
| sessions/ | [profile_manager.py](file:///c:/Users/MOM/Desktop/学习项目/CrawAgent/crawagent/sessions/profile_manager.py) | Playwright persistent_context 登录态持久化，跨会话复用 cookie |
| js_snippets/ | [navigator_overrider.js](file:///c:/Users/MOM/Desktop/学习项目/CrawAgent/crawagent/js_snippets/navigator_overrider.js) / [remove_overlay.js](file:///c:/Users/MOM/Desktop/学习项目/CrawAgent/crawagent/js_snippets/remove_overlay.js) | JS 注入片段（navigator 属性覆盖反检测 / 弹窗遮罩移除） |
| graph/ | [agent_workflow.py](file:///c:/Users/MOM/Desktop/学习项目/CrawAgent/crawagent/graph/agent_workflow.py) / [site_analyzer.py](file:///c:/Users/MOM/Desktop/学习项目/CrawAgent/crawagent/graph/site_analyzer.py) / [anti_bot.py](file:///c:/Users/MOM/Desktop/学习项目/CrawAgent/crawagent/graph/anti_bot.py) | 旧 LangGraph Agent 编排（兼容保留）；`AgentRunner`、`analyze_site()`、`is_blocked()`（三层反爬检测） |
| api/ | [server.py](file:///c:/Users/MOM/Desktop/学习项目/CrawAgent/crawagent/api/server.py) | FastAPI 服务，`_get_harness()` 单例装配 AntiBot + Security hooks |
| cli/ | [main.py](file:///c:/Users/MOM/Desktop/学习项目/CrawAgent/crawagent/cli/main.py) | Click CLI 命令 |
| config/ | [settings.py](file:///c:/Users/MOM/Desktop/学习项目/CrawAgent/crawagent/config/settings.py) | Pydantic Settings（MySQL/Redis/LLM/爬虫/Harness 配置），`get_settings()` 单例 |

### 4.10 graph/anti_bot.py — 反爬检测核心

`is_blocked(status_code, headers, body, error)` 实现三层反爬检测：

| 层 | 检测信号 | 输出 |
|----|----------|------|
| Layer 1 状态码 | 401/403/429/503 | `BlockResult`（blocked + challenge_type） |
| Layer 2 响应头 | Cloudflare/WAF 指纹 | challenge_type: cloudflare/waf |
| Layer 3 响应体 | CAPTCHA / JS 挑战 / WAF 拦截页 | recommended_action: use_curl_cffi/use_browser/wait_and_retry |

支持 7 种挑战类型：`cloudflare` / `waf` / `rate_limit` / `captcha` / `forbidden` / `js_challenge` / `none`。

---

## 5. 关键类与函数说明

### 5.1 CrawlHarness（编排层主类）

[文件](file:///c:/Users/MOM/Desktop/学习项目/CrawAgent/crawagent/harness/harness.py) — Agent Harness 编排层顶层入口，管理完整爬取任务生命周期。

| 方法 | 说明 |
|------|------|
| `create_session(name)` | 创建新 session，自动创建 `main` lane |
| `get_session(session_id)` | 获取已有 session（含缓存） |
| `create_lane(session_id, name, at_entry_id)` | 创建并行 lane |
| `set_tools(tools)` / `get_tools()` | 设置/获取工具集 |
| `register_tool_executor(session_id, tool_name, executor)` | 注册工具执行器到 loop |
| `prompt(session_id, message, lane_name)` → `RunResult` | **主交互入口**：用户输入 → Agent Loop → 返回结果 |
| `quick_crawl(url, instruction)` → `RunResult` | 快捷爬取：自动建 session + 运行，结果带回 session_id |
| `stop(session_id)` | 停止指定 session 的 loop |
| `close()` | 关闭所有资源 |

Phase 管理：`idle → turn → compact`。

### 5.2 CrawlLoop（驱动循环）

[文件](file:///c:/Users/MOM/Desktop/学习项目/CrawAgent/crawagent/harness/loop.py) — driverLoop 的 Python 实现。

核心循环：`checkpoint → step(LLM) → execute_tool_batch(三阶段) → followUp → finish`

- `_checkpoint()`：刷新写入 + 创建不可变 `TurnSnapshot` + 触发 Compaction
- `_step()`：组装上下文（老→新正序，保证 tool 消息紧跟 tool_calls）+ 组装系统提示词 + 调用 LLM + 解析响应 + 追加助手消息
- `_fix_tool_call_pairing()`（v1.1）：**工具调用完整性自愈**——消息被截取/压缩后若 assistant 有 tool_calls 但缺少对应 tool 响应，先移除该 assistant 消息，避免 OpenAI API 400 错误
- `_execute_tool_batch()`：批量执行（PARALLEL 模式并行，否则顺序），三阶段（prepare→execute→finalize）
- `_execute_single_tool()`：Security Hook 硬拦截 → 校验工具定义 → 调用 executor → 补充 tool_call_id（content 为空时自动填充默认文本）
- `_extract_follow_up()`：从 `[FOLLOW_UP:xxx]` 提取续跑指令
- 终止条件：LLM 不再产出 tool_calls / 达到 `max_turns` / 用户取消
- **消息兜底**（v1.1）：最终消息无 tool_calls 且 content 为空 → 自动填充 `"任务已完成。"`；工具结果 content 为空 → 填充错误信息或 `"工具执行完成（无返回内容）"`

### 5.3 CrawlSession（持久化会话）

[文件](file:///c:/Users/MOM/Desktop/学习项目/CrawAgent/crawagent/harness/session.py) — MySQL 持久化，支持崩溃恢复与多 lane 并行。

| 能力 | 方法 |
|------|------|
| 对话树（append-only） | `append_entry()` / `get_entries(limit, before_id, order)` / `get_leaf_entry()` |
| Lanes | `create_lane()` / `list_lanes()` / `update_lane_leaf()` / `set_lane_open_operation()` |
| 操作日志 | `start_operation()`（预分配 ID + 锚点记录）/ `finish_operation()` / `get_open_operations()` / `add_operation_entry()` / `heartbeat_operation()` |
| Global Facts | `get_fact()` / `set_fact()`（最新写胜）/ `list_facts()` |
| 崩溃恢复 | `build_recovery_plan()` / `apply_recovery_plan()` / `lane_chain_from_anchor()` |

恢复策略：COMPACTION 未完成 → MARK_FAILED；NAVIGATION → REPLAY_SAFE（幂等）；RUN → RETRY（上层根据 `replay_safe` 决定）。`SessionManager` 负责工厂创建、列举、删除（级联清理 entries/lanes/operation_logs/global_facts）。

### 5.4 RunResult（判别联合）

[文件](file:///c:/Users/MOM/Desktop/学习项目/CrawAgent/crawagent/harness/types.py) — 所有执行结果通过 `RunResult` 返回，不使用异常表示业务状态。

| kind | 含义 | 工厂方法 |
|------|------|----------|
| `completed` | 正常完成 | `RunResult.completed(data, follow_up)` |
| `needs_input` | 需要用户输入 | `RunResult.needs_input(follow_up)` |
| `error` | 发生错误 | `RunResult.from_error(error)` |
| `cancelled` | 已取消 | `RunResult.cancelled()` |

### 5.5 CrawlSupervisor（监督者调度器）

[文件](file:///c:/Users/MOM/Desktop/学习项目/CrawAgent/crawagent/harness/tools.py) — 编排 `crawl → analyze → extract → save` 的闭环协作，是 LLM 唯一可见的爬取入口。

执行流程：
1. **图片意图识别**（v1.1）：指令含"图片/壁纸/图库"等关键词 → 自动启用 `use_browser=True` + `method=images`，并调用 `extract_images()` 图片专用提取器
2. `crawl` → 获得 `page_signature` + html（Playwright 分支支持 SPA 客户端路由改写 + 滚动触发懒加载）
3. `clean` → `PruningContentFilter` 去噪 + `MarkdownGenerator` 生成 Markdown
4. 检查蓝图缓存（`ExtractionBlueprintCache`，按 page_signature LRU 缓存）
   - 命中 → 直接 extract
   - 未命中 → `analyze` 生成蓝图并缓存，再 extract
5. `extract`（带 `NEED_REANALYSIS` 重试，最多 2 次）
   - `confidence >= 70%` → save
   - 置信度不足 → 重新 analyze 失败字段，更新蓝图，重试
6. `save` → 自适应格式 + 去重 + 检查点 + 图片自动下载（`_maybe_download_images`）

工具间通信使用标准状态码（`STATUS_OK=200` / `STATUS_CACHED=201` / `STATUS_LOW_CONFIDENCE=422` / `STATUS_NEED_REANALYSIS=410` / `STATUS_FAILED=500`），而非抛异常。

**图片抓取总结**（v1.1）：HTTP 提取为空或疑似 SPA 时自动升级浏览器重抓；图片抓取完成后返回明确中文总结（条数/下载量/JSON 路径/图片目录/预览样例），避免 LLM 反复调用 search/supervisor 死循环。

### 5.6 内置工具集

[文件](file:///c:/Users/MOM/Desktop/学习项目/CrawAgent/crawagent/harness/tools.py) — `create_default_tools()` 注册 9 个工具。仅 `LLM_CALLABLE_TOOLS` 暴露给 LLM，其余由 Supervisor 内部编排。

| 工具 | exec_mode | replay_safe | 暴露 LLM | 用途 |
|------|-----------|-------------|----------|------|
| `crawl` | SEQUENTIAL | ✅ | ❌ | 抓取 URL（httpx→curl_cffi→Playwright） |
| `extract` | SEQUENTIAL | ✅ | ❌ | 自适应提取（含置信度反馈） |
| `analyze` | SEQUENTIAL | ✅ | ❌ | 站点结构分析，产出 ExtractionBlueprint |
| `save` | SEQUENTIAL | ❌ | ❌ | 智能存储路由（模板渲染+格式推断+去重+图片下载） |
| `supervisor` | SEQUENTIAL | ❌ | ✅ | **编排入口**：闭环协作 |
| `search` | PARALLEL | ✅ | ✅ | 本地全文搜索已爬数据 |
| `monitor` | SEQUENTIAL | ❌ | ✅ | 创建监控任务 |
| `check_change` | PARALLEL | ✅ | ✅ | 立即检查监控变化 |
| `scan_vuln` | SEQUENTIAL | ❌ | ✅ | OWASP Top 10 扫描 |
| `fix_issue` | SEQUENTIAL | ❌ | ✅ | 生成修复补丁 |

### 5.7 Compaction（上下文压缩）

[文件](file:///c:/Users/MOM/Desktop/学习项目/CrawAgent/crawagent/harness/compaction.py) — token 超阈值时自动压缩早期消息为摘要。

- `estimate_tokens()`：简单估算（中文约 2 字符/token，英文约 4，取平均 3 字符/token）
- `should_compact()`：触发 `BEFORE_COMPACTION` hook，含防抖（两次压缩间至少保留 `min_gap_between_compactions` 条消息）
- `compact()`：旧消息生成 LLM 摘要（失败回退规则摘要），合并旧 summary，写入 summary entry 并更新 lane leaf
- `run_checkpoint()`：超限自动压缩 + 返回重建后上下文
- `rebuild_context()`：从 session 拉取「最新一个 compaction summary + 后续消息」作为 LLM 上下文窗口

### 5.8 CrawlHooks（钩子系统）

[文件](file:///c:/Users/MOM/Desktop/学习项目/CrawAgent/crawagent/harness/hooks.py) — hook 注册与链式执行。

- `on(event, callback, priority)`：注册（priority 数字小先执行）
- `off(event, callback)`：移除
- `fire(event, context)`：按优先级链式执行，前一个回调返回值更新 context

内置 `AntiBotHookHandler`：`before_tool` 注入 `use_browser`/`timeout` 升级策略，`after_tool` 检测 403/429/Cloudflare/WAF 并存储挑战状态，200 响应清除状态。`create_antibot_hooks(hooks)` 一键注册。

---

## 6. 依赖关系

### 6.1 模块间依赖

```
api/server.py
   ├─ harness (CrawlHarness, SessionManager, hooks, tools)
   │   ├─ core.database (Base, get_db)
   │   ├─ llm.factory (get_llm)
   │   └─ graph.anti_bot (is_blocked)
   ├─ security.security_hook (create_security_hooks)
   ├─ core (fetcher, frontier, extractor, job_store, models)
   ├─ graph (agent_workflow, site_analyzer, anti_bot)
   └─ config.settings (get_settings)

harness.loop
   ├─ harness.session / hooks / types
   ├─ harness.compaction (Compaction)
   ├─ harness.system_prompt (SystemPromptAssembler)
   ├─ harness.tools (to_langchain_tools)
   └─ llm.factory (get_llm)

harness.tools (executors)
   ├─ core.fetcher (Fetcher)
   ├─ core.extractor (CompositeExtractor)
   ├─ core.content_filter / markdown_generator (P2 清洗)
   ├─ output.organizer (FileOrganizer)
   ├─ output.media_downloader (MediaDownloader)
   ├─ graph.site_analyzer (analyze_site_simple)
   ├─ monitor (MonitorTask, MonitorScheduler)
   └─ security (SecurityLane, AutoFixer)

engines.fallback
   ├─ engines.httpx_engine / curl_cffi_engine / playwright_engine
   └─ graph.anti_bot (is_blocked)
```

### 6.2 外部依赖（pyproject.toml）

| 类别 | 依赖 |
|------|------|
| 核心框架 | langchain-core、langgraph、langchain-openai |
| HTTP 客户端 | httpx、curl-cffi（可选 stealth） |
| HTML 解析 | beautifulsoup4、selectolax、lxml、w3lib、html2text、markdownify |
| 配置/序列化 | pydantic、pydantic-settings、python-dotenv、msgspec |
| 数据库 | sqlalchemy[asyncio]、aiomysql、aiosqlite |
| 缓存/队列 | redis |
| 调度 | apscheduler |
| 媒体下载 | yt-dlp |
| 重试 | tenacity |
| Web 框架 | fastapi、uvicorn[standard] |
| CLI | click、rich、prompt-toolkit |
| 日志 | loguru、structlog |
| 工具 | xxhash |
| 可选 browser | playwright、patchright |
| 可选 stealth | browserforge |
| 可选 data | pandas、orjson、trafilatura |

完整安装：`pip install -e ".[all]"`（等同 `[browser,stealth,data]`）。构建系统：setuptools + wheel，目标 Python 3.11。

### 6.3 前端依赖（package.json）

Vue 3.4 + vue-router 4.3 + element-plus 2.7 + @element-plus/icons-vue + axios。构建工具 Vite 5.4。

---

## 7. 数据流

### 7.1 爬取任务主流程

```
用户输入 → POST /api/harness/prompt
  → CrawlHarness.prompt(session_id, message)
    → 创建/恢复 CrawlSession
    → SystemPromptAssembler 组装 system prompt（四段式）
    → CrawlLoop.run()
      → checkpoint（刷新 + 创建 TurnSnapshot + 触发 Compaction）
      → step：组装上下文（老→新）+ 绑定工具 + LLM tool calling
      → execute_tool_batch（三阶段，PARALLEL 并行）
        → LLM 调用 supervisor → CrawlSupervisor.run
          → crawl（FallbackChain）→ clean（PruningContentFilter+MarkdownGenerator）
          → analyze（生成 ExtractionBlueprint）→ extract（置信度反馈）
          → save（模板渲染+格式推断+去重+图片下载）
      → followUp：判断是否继续
      → 循环直到 finish / max_turns / cancel
    → RunResult 返回
```

### 7.2 Session 持久化

```
对话树 append-only
  → MySQL harness_entries 表（每条 entry 不可变，只追加）
  → harness_lanes 记录 leaf 指针（指向各分支最新 entry）
  → harness_global_facts 最新写胜（覆盖更新）
  → harness_operation_logs 预分配 ID + 锚点（at-least-once 语义）
```

### 7.3 崩溃恢复

```
operation_logs（每步操作前预写日志 + 预分配 ID）
  → 进程崩溃后重启
  → CrawlSession.build_recovery_plan()
    → get_open_operations() 查询未完成操作
    → 识别孤儿 tool_call（有 tool_call 发起但无 tool 结果消息）
    → 按 op_type 决定 RecoveryAction：
        COMPACTION → MARK_FAILED（幂等）
        NAVIGATION → REPLAY_SAFE（HTTP GET 幂等）
        RUN → RETRY（上层按 replay_safe 决定）
  → apply_recovery_plan() 收尾
  → lane_chain_from_anchor() 从锚点重建上下文
  → 继续执行未完成的工具调用
```

---

## 8. 前端结构

Vue 3 + Vite 单页应用，侧边栏导航 + `<router-view>` 主内容区（`<keep-alive>` 缓存）。

### 8.1 路由

[router/index.js](file:///c:/Users/MOM/Desktop/学习项目/CrawAgent/frontend/src/router/index.js)

| 路径 | 页面 | 功能 |
|------|------|------|
| `/chat` | Chat.vue | Agent 交互主界面（默认重定向 `/` → `/chat`） |
| `/tasks` | Tasks.vue | 任务列表管理 |
| `/settings` | Settings.vue | 系统配置 |
| `/output` | Output.vue | 文件整理 + 预览（路径双击打开文件夹） |
| `/monitor` | Monitor.vue | 监控任务 + 告警 |
| `/security` | Security.vue | 漏洞扫描 + 修复 |
| `/video` | Video.vue | 视频下载 + 本地播放 |

### 8.2 关键文件

| 文件 | 职责 |
|------|------|
| [App.vue](file:///c:/Users/MOM/Desktop/学习项目/CrawAgent/frontend/src/App.vue) | 应用外壳（侧边栏 + 状态指示器） |
| [api/index.js](file:///c:/Users/MOM/Desktop/学习项目/CrawAgent/frontend/src/api/index.js) | axios 封装的 API 客户端 |
| [main.js](file:///c:/Users/MOM/Desktop/学习项目/CrawAgent/frontend/src/main.js) | 应用入口 |
| [styles/theme.css](file:///c:/Users/MOM/Desktop/学习项目/CrawAgent/frontend/src/styles/theme.css) | 主题样式 |

---

## 9. 项目运行方式

### 9.1 环境要求

- Python 3.11+
- Node.js（前端构建）
- MySQL 8.0 + Redis 7（建议用 docker-compose 启动）

### 9.2 安装

```bash
# 核心依赖
pip install -e .

# 可选：浏览器引擎（反爬/JS渲染需要）
pip install playwright && playwright install chromium

# 可选：TLS 指纹绕过
pip install curl_cffi

# 可选：视频下载
pip install yt-dlp

# 完整安装（browser + stealth + data）
pip install -e ".[all]"
```

### 9.3 配置

复制 `.env.example` 为 `.env`，关键配置项：

```bash
OPENAI_API_KEY=your_key
OPENAI_BASE_URL=https://api.deepseek.com/v1   # 任意 OpenAI 兼容 API
DEFAULT_MODEL=deepseek-chat
MOCK_MODE=false                                 # true 时用 MockLLM，无需 API key

# MySQL
MYSQL_HOST=localhost
MYSQL_PORT=3306
MYSQL_USER=crawagent
MYSQL_PASSWORD=crawagent
MYSQL_DATABASE=crawagent

# Redis
REDIS_URL=redis://localhost:6379/0

# Agent Harness
HARNESS_MAX_TURNS=50
HARNESS_COMPACTION_THRESHOLD=80000              # token 数
```

启动 MySQL + Redis：

```bash
docker-compose up -d mysql redis
```

### 9.4 运行

```bash
# 启动后端 API（默认 0.0.0.0:8000）
python -m crawagent.api.server
# 或
python main.py serve --host 0.0.0.0 --port 8000 --reload

# 启动前端（默认 5173）
cd frontend && npm install && npm run dev
```

启动后访问 Swagger UI：`http://localhost:8000/docs`

### 9.5 CLI 用法

[main.py](file:///c:/Users/MOM/Desktop/学习项目/CrawAgent/main.py) 提供以下命令：

| 命令 | 用途 |
|------|------|
| `python main.py run <instruction> [--url URL] [-p MAX_PAGES] [-t THREAD] [-o OUTPUT]` | 交互式运行 Agent（经 AgentRunner） |
| `python main.py analyze <url> [-o OUTPUT]` | 分析单个站点结构 |
| `python main.py crawl <urls...> [-s field=selector] [-p MAX_PAGES] [-o OUTPUT]` | 直接爬取（不经 Agent 规划） |
| `python main.py serve [--host] [--port] [--reload]` | 启动 API 服务 |
| `python main.py init` | 初始化数据目录 |
| `python main.py check` | 检查依赖与配置（含 curl_cffi/playwright/browserforge/lxml 可选包检测） |

### 9.6 API 端点分组

[server.py](file:///c:/Users/MOM/Desktop/学习项目/CrawAgent/crawagent/api/server.py) 注册约 51 个端点，按功能分组：

| 分组 | 前缀 | 说明 |
|------|------|------|
| 健康/统计 | `/api/health`、`/api/stats` | 健康检查、系统统计 |
| Agent 任务 | `/api/agent/*` | 旧 Agent 工作流（run/status/jobs/analyze） |
| 直接爬取 | `/api/crawl/direct`、`/api/jobs/*` | 不经 Agent 的直接爬取与任务查询 |
| Harness | `/api/harness/*` | pi Agent Harness（sessions/prompt/quick-crawl/lanes/entries） |
| 反爬 | `/api/anti-bot/handle` | 反爬挑战处理 |
| 输出 | `/api/output/*` | 文件列举/预览/打开/删除 |
| 监控 | `/api/monitor/*` | 监控任务/告警/Webhook 测试 |
| 安全 | `/api/security/*` | 扫描任务/漏洞/补丁 |
| 视频 | `/api/video/*` | 下载/信息/提取/广告移除/文件/流播放 |
| 设置 | `/api/settings`（GET/PUT） | 系统配置读写 |

> 完整端点签名详见 Swagger UI（`/docs`），本文不逐一列举。

### 9.7 测试

```bash
pytest                  # 全部测试（asyncio_mode=auto）
pytest tests/test_p6.py # 单个测试文件
```

测试套件覆盖 chunking、content_filter、deep_crawl、extractor、markdown_generator、monitor、output、prompts_usage、security、video 等模块，含单元测试、脏数据测试与端到端测试。

### 9.8 Docker 部署

[docker-compose.yml](file:///c:/Users/MOM/Desktop/学习项目/CrawAgent/docker-compose.yml) 定义 MySQL 8.0（utf8mb4）与 Redis 7（appendonly + LRU 256mb），均含健康检查与数据卷持久化。MySQL 启动时执行 [docker/mysql/init.sql](file:///c:/Users/MOM/Desktop/学习项目/CrawAgent/docker/mysql/init.sql) 初始化。P8 阶段提供完整 Docker/Nginx 部署方案（本地可选）。

---

## 10. 设计模式与扩展点

### 10.1 关键设计模式

| 模式 | 实现 |
|------|------|
| Waterfall Fallback | [FallbackChain](file:///c:/Users/MOM/Desktop/学习项目/CrawAgent/crawagent/engines/fallback.py) 三引擎依次尝试 + 反爬检测自动升级 |
| Strategy Pattern | 7 种 Chunking 策略、8 种 Extractor、3 种 ContentFilter |
| Chain of Responsibility | [FilterChain](file:///c:/Users/MOM/Desktop/学习项目/CrawAgent/crawagent/core/filters.py) URL 过滤器链 |
| Factory Pattern | [LLMFactory](file:///c:/Users/MOM/Desktop/学习项目/CrawAgent/crawagent/llm/factory.py) 多提供商、`create_default_tools()` |
| Supervisor 编排 | [CrawlSupervisor](file:///c:/Users/MOM/Desktop/学习项目/CrawAgent/crawagent/harness/tools.py) 工具闭环协作 + 蓝图缓存 + 置信度反馈 |
| Event-Driven Hooks | [CrawlHooks](file:///c:/Users/MOM/Desktop/学习项目/CrawAgent/crawagent/harness/hooks.py) 8 种事件链式执行 |
| Append-Only Log | [CrawlSession](file:///c:/Users/MOM/Desktop/学习项目/CrawAgent/crawagent/harness/session.py) 对话树 + operation_logs |
| Write-Ahead Logging | 操作执行前预写日志 + 预分配 ID（at-least-once） |
| LRU Cache | ExtractionBlueprintCache（按 page_signature 缓存提取蓝图） |

### 10.2 扩展点

| 扩展点 | 方式 |
|--------|------|
| 新工具 | 注册 `CrawlToolDef` + executor 到 `ToolRegistry`，加入 `LLM_CALLABLE_TOOLS` 暴露给 LLM |
| 新 Hook | 实现 `HookCallback`，调用 `hooks.on(event, callback, priority)` |
| 新 Lane | `session.create_lane(name)`（main/monitor/security 等并行分支） |
| 新引擎 | 实现 `BaseEngine` + 加入 `DEFAULT_ENGINE_ORDER` 或 `build_fallback_list()` |
| 新 LLM Provider | 实现 `langchain BaseChatModel` + 注册到 `LLMFactory` |
| 新提取策略 | 继承 `BaseExtractor`，注册到 `CompositeExtractor` |
| 新 URL 过滤器 | 继承 `URLFilter`，加入 `FilterChain` |
| 新分块策略 | 继承 `ChunkingStrategy`，注册到 `create_chunker()` |
| 新内容过滤器 | 继承 `RelevantContentFilter`，注册到 `create_content_filter()` |

### 10.3 运维与监控

- **日志**：loguru + structlog 结构化日志，敏感数据（Cookie/Token/完整响应体）不记录
- **关键指标**：`pages_crawled` / `items_extracted` / `errors` / `duration`、域名级成功率与延迟、Session 活跃数与 Lane 并行度、LLM 调用次数与 token 消耗
- **故障恢复**：operation_logs 预分配 ID + 未完成操作检测 → 自动续跑；Save Point 每轮刷新；MySQL 持久化保证进程重启不丢失
- **安全合规**：域名限速（令牌桶，默认 0.5 req/s）、代理轮换、UA 轮换、敏感数据不记录、可选 robots.txt 检查、仅个人使用

---

## 11. 版本历史

| 版本 | 日期 | 变更 |
|------|------|------|
| v1.0 | 2026-07-12 | 初始版本：LangGraph 状态机架构，5 阶段实现 |
| v2.0 | 2026-07-31 | pi Agent Harness 重构：CrawlHarness/CrawlLoop/CrawlSession/CrawlHooks/Compaction 等新模块，MySQL 持久化，8 种 hook，7 个内置工具 |
| v2.1 | 2026-08-01 | P1 引擎抽象层、P6 深度爬取+URL过滤器+饱和度感知、P7 视频+广告移除+yt-dlp、代理轮换、登录态持久化、反爬 Hook |
| v2.2 | 2026-08-02 | 图片专用提取器（extract_images 多源提取）、SPA 客户端路由、网站画像系统（site_profile.py）、工具调用完整性自愈、Supervisor 自动浏览器升级 |
| v2.3 | 2026-08-02 | P8 登录态 API（手动登录 + 匿名 cookie 自动获取 + cookies.txt 导出）、MediaDownloader 自动兜底匿名 cookie、P9 浏览器 API 捕获器（api_harvester.py 拦截签名 API，无需逆向算法）、harvest_api 工具 |

---

## 附录：相关文档

- [README.md](file:///c:/Users/MOM/Desktop/学习项目/CrawAgent/README.md) — 项目简介与快速开始
- [ARCHITECTURE.md](file:///c:/Users/MOM/Desktop/学习项目/CrawAgent/ARCHITECTURE.md) — 详细架构设计、数据流、接口定义
- [DEVELOPMENT_PLAN.md](file:///c:/Users/MOM/Desktop/学习项目/CrawAgent/DEVELOPMENT_PLAN.md) — 开发路线图与验收标准
- [KNOWN_GAPS.md](file:///c:/Users/MOM/Desktop/学习项目/CrawAgent/KNOWN_GAPS.md) — 已知缺口清单

---

*本文档随代码演进同步更新。维护时请在对应模块源码处补充交叉引用。*
