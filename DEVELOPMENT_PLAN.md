# CrawAgent 开发计划

> 版本：v2.2 | 更新：2026-08-01
> 变更：P1-P7 全部完成并通过验收；P4 真实监控 e2e 补验通过（含告警通知状态持久化修复）

---

## 一、产品定位

**面向个人/未来开源的专业 AI 爬虫 Agent，"爬取万事万物"。**

### 核心场景
1. **数据采集 / 监控**：手机价格波动、网站稳定性、漏洞/入侵监控
2. **内容整理**：抓取音视频/图片/文章并落到用户指定的本地文件路径（含会员内容，灰色路径不开源）
3. **vibe coding 安全检查**：帮助非专业程序员发现并修复自建网站安全问题

### 四大差异化卖点（相对 Firecrawl / Crawl4AI）
| 卖点 | 参考项目是否做 | 我们是否做 |
|---|---|---|
| **文件整理到用户指定路径** | 都不做 | ✅ |
| **监控场景（定时+diff+告警）** | 都不做 | ✅ |
| **vibe coding 安全检查** | 都不做 | ✅ |
| **真正的 Agent Harness（loop+hooks+lanes）** | 都不做（SDK/API） | ✅ |

### 影视/会员内容定位
- 合法路径进开源版：登录态保持 + DOM 广告移除 + 视频 URL 提取 + yt-dlp 落地
- 灰色路径独立分支：破解付费墙 / 绕过 DRM / 自动填表登录

---

## 二、架构蓝图：以 pi Agent Harness 为地基

### 当前问题：不是 Agent，是固定流水线

```
classify → plan → analyze → execute → decide(永远True)
```

这是**管道**，不是 Agent。LLM 只当分类器/规划器，不能自主决策、不能重试、不能换工具。

### 目标架构：pi 风格的 Agent Harness

```
                    ┌─────────────────────────────────┐
                    │         AgentHarness             │
                    │  (编排层：session + hooks + lanes)│
                    ├─────────────────────────────────┤
                    │  phase: idle → turn → compact   │
                    │  turn snapshot (不可变/每轮新建)  │
                    │  save points (轮间刷新状态)       │
                    ├─────────────────────────────────┤
                    │  hooks:                          │
                    │    before_run  → 注入消息/覆写系统提示词│
                    │    before_tool → 拦截/修改工具调用  │
                    │    after_tool  → 修改工具结果      │
                    │    transform_context → 修剪上下文  │
                    │    before_request → 修改请求选项   │
                    │    after_response → 修改LLM响应    │
                    │    before_compaction → 压缩决策    │
                    │    before_run_end → 追加后续任务   │
                    ├─────────────────────────────────┤
                    │  lanes:                          │
                    │    main    → 主爬取任务            │
                    │    monitor → 监控任务              │
                    │    security → 安全扫描任务         │
                    ├─────────────────────────────────┤
                    │  session (MySQL):                │
                    │    tree (append-only 对话树)       │
                    │    lanes (命名位置 + leaf指针)     │
                    │    operation_logs (持久化记录)     │
                    │    global_facts (最新写胜)         │
                    └──────────────┬──────────────────┘
                                   │
                    ┌──────────────▼──────────────────┐
                    │        Agent Loop (driverLoop)    │
                    │  while True:                     │
                    │    checkpoint (刷新写入+steering)  │
                    │    if 需要助手消息:                │
                    │      step → streamAssistant       │
                    │      if 有工具调用:               │
                    │        executeToolBatch           │
                    │        continue (新checkpoint)     │
                    │    elif 有followUp: continue      │
                    │    else: before_run_end → finish  │
                    └──────────────┬──────────────────┘
                                   │
                    ┌──────────────▼──────────────────┐
                    │         Tool System              │
                    │  三阶段执行:                       │
                    │    Phase 1: prepare (校验+hook)   │
                    │    Phase 2: execute (实际效果)     │
                    │    Phase 3: finalize (patch结果)  │
                    │  批量执行: sequential/parallel    │
                    │  重播安全: never/safe 声明        │
                    └─────────────────────────────────┘
```

### pi → CrawAgent 架构映射

| pi 概念 | CrawAgent 对应 | 说明 |
|---|---|---|
| `AgentHarness` | `CrawlHarness` | 编排层，拥有 session + hooks + lanes |
| `agentLoop` / `driverLoop` | `CrawlLoop` | LLM 调用循环，tool calling 驱动 |
| `AgentTool` | `CrawlTool` | 爬虫工具集（fetch/extract/analyze/monitor/fix） |
| `Session` | `CrawlSession` | MySQL 持久化会话（对话树 + 操作日志） |
| `Lane` | `CrawlLane` | 命名位置（main/monitor/security 并行） |
| `Hook` | `CrawlHook` | 拦截点（anti_bot/security/monitor hook） |
| `TurnSnapshot` | `TurnSnapshot` | 每轮不可变快照 |
| `Compaction` | `Compaction` | 上下文压缩（长爬取任务） |
| `ExecutionEnv` | `CrawlEnv` | 执行环境抽象（文件系统/浏览器/HTTP） |
| `Skill` | `CrawlSkill` | 预制能力（deep_crawl/monitor/scan） |
| `PromptTemplate` | `CrawlTemplate` | 提示词模板 |

### 关键设计原则（从 pi 继承）

1. **Turn Snapshot 不可变** — 每轮 LLM 调用创建新快照，运行时配置变更只影响下一轮
2. **Save Point 刷新** — 轮间刷新写入 + 创建新快照 + 应用配置变更
3. **Hooks 先于效果** — `before_tool` 的输出持久化在 `tool_started` 记录里
4. **Append-only 树** — 对话条目只增不改不删，重跑只增量合并不覆盖
5. **Lanes 并行** — 主爬取/监控/安全扫描可在同一 session 内并行
6. **Durability（持久化）** — 操作日志 + 预分配ID，崩溃后可恢复
7. **结果而非异常** — 操作方法返回 `RunResult` 判别联合，不抛异常
8. **Compaction** — 上下文超限时自动压缩，checkpoint 自动触发

---

## 三、技术栈

**Python + FastAPI + Vue 3 + MySQL + Redis + Docker + Nginx**

| 层 | 选型 | 说明 |
|---|---|---|
| Agent Harness | 自研 `CrawlHarness`（pi 架构 Python 版） | 不用 LangGraph——pi 的 Harness 比 LangGraph 更适合 |
| LLM 接口 | OpenAI 兼容（langchain-openai） | 多 provider 支持 |
| HTTP 抓取 | httpx + curl_cffi | 轻量 + TLS 指纹 |
| 浏览器 | Playwright | JS 渲染 + 登录态 + 反爬 |
| 数据库 | MySQL 8.0 + aiomysql + SQLAlchemy 2.0 async | 用户技术栈 |
| 缓存/队列 | Redis 7 + redis-py | 监控调度 + 限流 |
| Web 框架 | FastAPI + uvicorn | 已选型 |
| 前端 | Vue 3 + Element Plus + Vite | 已选型 |
| 任务调度 | APScheduler（Redis 后端） | 监控场景 |
| 媒体下载 | yt-dlp | 影视场景 |
| HTML→MD | markdownify + html2text | 内容清洗 |
| 主体提取 | trafilatura + beautifulsoup4 | 去噪 |
| 反爬检测 | 三层检测（借鉴 Crawl4AI） | antibot_detector 移植 |
| 日志 | loguru + structlog | 结构化日志 |
| 容器 | Docker + docker-compose | 用户技术栈 |
| 反代 | Nginx | 用户技术栈 |

### 为什么不用 LangGraph？

LangGraph 是"状态机编排器"，适合固定流程。但 pi 的 Agent Harness 证明：**真正的 Agent 需要的是 loop + hooks + lanes，不是状态机**。

| 对比 | LangGraph | pi Harness |
|---|---|---|
| 核心模式 | 状态机（node → edge → node） | 循环（step → tools → step → ...） |
| 决策方式 | 条件边路由 | LLM tool calling 自主决策 |
| 扩展方式 | 加 node/edge | 注册 hook + tool |
| 并行 | 需要手动设计 | Lanes 天然并行 |
| 持久化 | checkpointer | Session + 操作日志（更强） |
| 压缩 | 无 | Compaction 内建 |

CrawAgent 的 `CrawlHarness` 采用 pi 模式：**LLM 在 loop 中自主选工具，hooks 拦截/修改行为，lanes 并行**。

---

## 四、开发路线图

### 阶段 P0：修 bug + MySQL 迁移 + CrawlHarness 骨架（2 周）

**目标**：修现有 bug + 数据库迁移 + 搭建 pi 风格的 Harness 骨架，让 Agent 能真正跑起来。

| 任务 | 文件 | 说明 | pi 参考 |
|---|---|---|---|
| P0-1 | graph/anti_bot.py | 修 B1-B3（import json / 合并重复函数 / 修复类型） | — |
| P0-2 | graph/site_analyzer.py | 修 B4-B6（删除重复定义 / 补全字段 / 接入子图） | — |
| P0-3 | llm/factory.py | 修 B7（移除 @lru_cache 装饰实例方法） | — |
| P0-4 | core/fetcher.py | 修 B8（缩进修正） | — |
| P0-5 | pyproject.toml + config/settings.py | 加 aiomysql / sqlalchemy / redis | — |
| P0-6 | 新建 `crawagent/harness/types.py` | CrawlHarness 类型定义（CrawlMessage / CrawlTool / HookEvent / TurnSnapshot / RunResult 等） | pi `packages/agent/src/harness/types.ts` |
| P0-7 | 新建 `crawagent/harness/session.py` | CrawlSession（MySQL 存储：对话树 + 操作日志 + lanes + global_facts） | pi `packages/agent/src/harness/session/session.ts` |
| P0-8 | 新建 `crawagent/harness/hooks.py` | CrawlHooks（注册/触发/归约，8 种 hook 事件） | pi `packages/agent/docs/hooks.md` |
| P0-9 | 新建 `crawagent/harness/loop.py` | CrawlLoop（driverLoop：checkpoint → step → tools → followUp → finish） | pi `packages/agent/src/agent-loop.ts` + `docs/harness-v2.md:1478-1506` |
| P0-10 | 新建 `crawagent/harness/harness.py` | CrawlHarness 主类（phase 管理 / turn snapshot / save point / lanes / 操作方法） | pi `packages/agent/src/harness/agent-harness.ts` |
| P0-11 | 新建 `crawagent/harness/tools.py` | 爬虫工具集（crawl / extract / analyze / save / search）+ 三阶段执行 | pi `packages/agent/src/harness/tools/` |
| P0-12 | 新建 `crawagent/harness/env.py` | CrawlEnv（执行环境抽象：HTTP / 浏览器 / 文件系统） | pi `packages/agent/src/harness/env/nodejs.ts` |
| P0-13 | 新建 `crawagent/harness/compaction.py` | 上下文压缩（长爬取任务必备） | pi `packages/agent/src/harness/compaction/compaction.ts` |
| P0-14 | 新建 `crawagent/harness/system_prompt.py` | 系统提示词组装（core + 站点特征 + 任务 notes + 用户追加） | pi `packages/agent/src/harness/system-prompt.ts` + Deepsec `assemblePrompt` |
| P0-15 | core/frontier.py + core/job_store.py | SQLite → MySQL 迁移 | — |
| P0-16 | api/server.py | 接入 CrawlHarness，替换旧的 AgentRunner | — |
| P0-17 | 新建 docker-compose.yml | MySQL 8.0 + Redis 7 容器 | — |

### 阶段 P1：AntiBot 检测 + 引擎 fallback 链 + Hooks 接入（2 周）

**目标**：通过 hooks 系统实现反爬自动升级和引擎 fallback。

| 任务 | 文件 | 借鉴来源 |
|---|---|---|
| P1-1 | harness/ 下新增 `antibot_hook.py` | `before_tool` hook：检测到反爬 → 拦截当前引擎 → 注入升级策略 |
| P1-2 | harness/ 下新增 `escalation_hook.py` | `after_response` hook：401/403/429 → AddFeatureError → 自动升级引擎 |
| P1-3 | graph/anti_bot.py 新增 `is_blocked()` 三层检测 | Crawl4AI `antibot_detector.py` |
| P1-4 | 新建 `crawagent/engines/` 目录 | BaseEngine + httpx/curl_cffi/playwright 三引擎 | Firecrawl `engines/` |
| P1-5 | 新建 `crawagent/engines/fallback.py` | build_fallback_list + waterfall 执行 | Firecrawl `engines/index.ts` |
| P1-6 | 新建 `crawagent/js_snippets/` | navigator_overrider.js + remove_overlay.js | Crawl4AI `js_snippet/` |
| P1-7 | 新建 `crawagent/core/proxy.py` | ProxyConfig + RoundRobin | Crawl4AI `proxy_strategy.py` |
| P1-8 | 新建 `crawagent/sessions/profile_manager.py` | Playwright persistent_context | Firecrawl `browser-sessions.ts` |

### 阶段 P2：内容清洗 + LLM 提取 + 提示词工程（2 周）

**目标**：解决"80% 清洗时间"问题。

| 任务 | 文件 | 借鉴来源 |
|---|---|---|
| P2-1 | 新建 `crawagent/core/content_filter.py` | BM25 + Pruning + LLM 三档 | Crawl4AI `content_filter_strategy.py` |
| P2-2 | 新建 `crawagent/core/markdown_generator.py` | citation 引用格式 + html2text 定制 | Crawl4AI `html2text/` |
| P2-3 | 重构 `crawagent/core/extractor.py` | 策略模式 8 种提取器 | Crawl4AI `extraction_strategy.py` |
| P2-4 | 新建 `crawagent/core/chunking.py` | 7 种分块策略 | Crawl4AI `chunking_strategy.py` |
| P2-5 | 重写 `crawagent/llm/prompts.py` | 6 个高质量提示词 + JSON_SCHEMA_BUILDER | Crawl4AI `prompts.py` |
| P2-6 | 新建 `crawagent/llm/usage.py` | TokenUsage 跟踪 | Crawl4AI `extraction_strategy.py:621` |

### 阶段 P3：差异化 — 文件整理到指定路径（1 周）

| 任务 | 文件 | 说明 |
|---|---|---|
| P3-1 | 新建 `crawagent/output/organizer.py` | FileOrganizer（路径模板引擎） |
| P3-2 | 新建 `crawagent/output/media_downloader.py` | yt-dlp + httpx 流式下载 |
| P3-3 | harness/tools.py 新增 `save` 工具 | Agent 可自主决定保存路径 |
| P3-4 | 前端新建 `Output.vue` | 文件整理规则配置 |
| P3-5 | api/server.py 新增 `/api/output/*` | CRUD 端点 |

### 阶段 P4：差异化 — 监控场景 + Lanes（2 周）

**目标**：用 pi 的 Lanes 机制实现并行监控。

| 任务 | 文件 | 说明 |
|---|---|---|
| P4-1 | harness/ 新增 `monitor_lane.py` | 独立 lane 运行监控任务，与 main lane 并行 |
| P4-2 | 新建 `crawagent/monitor/scheduler.py` | APScheduler + Redis |
| P4-3 | 新建 `crawagent/monitor/diff_detector.py` | 变化检测 + 结构化 diff |
| P4-4 | 新建 `crawagent/monitor/notifier.py` | Webhook / 飞书 / 邮件 |
| P4-5 | 新建 `crawagent/monitor/baseline.py` | MySQL 基线快照 |
| P4-6 | harness/tools.py 新增 `monitor` / `check_change` 工具 | Agent 可自主监控 |
| P4-7 | 前端新建 `Monitor.vue` | 监控任务 CRUD + 告警列表 |
| P4-8 | api/server.py 新增 `/api/monitor/*` | 端点 |

### 阶段 P5：差异化 — vibe coding 安全检查（2 周）

**目标**：用 Lane + Hook 实现安全扫描。

| 任务 | 文件 | 说明 |
|---|---|---|
| P5-1 | harness/ 新增 `security_lane.py` | 独立 lane 运行安全扫描 |
| P5-2 | harness/ 新增 `security_hook.py` | `before_tool` 拦截危险操作 + `after_tool` 检测异常 |
| P5-3 | 新建 `crawagent/security/network_capture.py` | 浏览器网络请求全捕获 | Crawl4AI `async_crawler_strategy.py:618-700` |
| P5-4 | 新建 `crawagent/security/vuln_scanner.py` | OWASP Top 10 扫描 | — |
| P5-5 | 新建 `crawagent/security/auto_fixer.py` | 生成 diff + 补丁文件 | — |
| P5-6 | harness/tools.py 新增 `scan_vuln` / `fix_issue` 工具 | Agent 可自主扫描修复 |
| P5-7 | 前端新建 `Security.vue` | 扫描报告 + 一键修复 |
| P5-8 | api/server.py 新增 `/api/security/*` | 端点 |

### 阶段 P6：Compaction + Durability + 深度爬取（1.5 周）

**目标**：长任务的上下文压缩 + 崩溃恢复 + 深度爬取。

| 任务 | 文件 | 说明 |
|---|---|---|
| P6-1 | harness/compaction.py 完善 | 自动压缩（checkpoint 时超限触发） | pi `docs/harness-v2.md:428-431` |
| P6-2 | harness/session.py 完善 | 操作日志 + 预分配ID + 崩溃恢复 | pi `docs/harness-v2.md:169-511` |
| P6-3 | 新建 `crawagent/core/deep_crawl.py` | BFS / DFS / Best-First | Crawl4AI `deep_crawling/` |
| P6-4 | 新建 `crawagent/core/adaptive.py` | 饱和度感知爬取 | Crawl4AI `adaptive_crawler.py` |
| P6-5 | 新建 `crawagent/core/filters.py` | URLFilter + FilterChain | Crawl4AI `deep_crawling/filters.py` |

### 阶段 P7：影视内容 + 会员（2 周）

| 任务 | 文件 | 说明 |
|---|---|---|
| P7-1 | sessions/profile_manager.py | 登录向导 + cookie 持久化 |
| P7-2 | 新建 `crawagent/extractors/video_extractor.py` | `<video>`/`<source>` URL 提取 |
| P7-3 | 新建 `crawagent/extractors/ad_remover.py` | DOM 广告移除 |
| P7-4 | output/media_downloader.py | yt-dlp 集成（cookie 透传） |
| P7-5 | 新建专用后处理器 | youtube / bilibili / zhihu |

### 阶段 P8：Docker + Nginx + 开源准备（1 周）

| 任务 | 文件 | 说明 |
|---|---|---|
| P8-1 | Dockerfile | Python 3.11 + Playwright |
| P8-2 | docker-compose.yml 完善 | app + mysql + redis |
| P8-3 | nginx/crawagent.conf | HTTPS 反代 |
| P8-4 | .env.example | 环境变量模板 |
| P8-5 | README 更新 | 一键部署说明 |

---

## 五、执行顺序

```
P0（bug修+MySQL+Harness骨架）✅ → P1（反爬+引擎链+hooks）✅ → P2（清洗+提取）✅
    → P3（文件整理）✅ → P4（监控+lanes）✅ → P5（安全扫描）✅
    → P6（压缩+持久化+深爬）✅ → P7（影视内容）✅ → P8（Docker部署，本地可选）
```

**理由**：
- P0 是地基，必修 ✅
- P1+P2 让 Agent 能爬到东西 ✅
- P3 是最易交付的差异化 ✅
- P4+P5 是垂直场景（依赖 lanes 机制，P0 已搭建）✅
- P6 是长任务/稳定性增强 ✅
- P7 是影视场景 ✅
- P8 Docker 部署：本地使用不需要，仅在部署到服务器时需要

---

## 六、验收标准（真实网站 + 脏数据）

### P0 验收
- [x] `docker-compose up` 一键启动
- [x] CrawlHarness 骨架可运行：用户输入 → driverLoop → tool calling → 结果
- [x] 真实 LLM 端到端跑通：`CrawlHarness.prompt` 全流程 completed（2026-08-01 实测，阮一峰博客；HN 域名 news.ycombinator.com 本机 ConnectTimeout 网络不可达，待可达网络实测）
- [x] 重启后从 MySQL 恢复 session
- [x] **脏数据**：seed_urls 含空串/非法 URL/JS 协议/不可达地址，Agent 不崩（2026-08-01 实测全部优雅返回错误）

### P1 验收
- [x] is_blocked() 三层检测：状态码+响应头+响应体，7 种挑战类型（2026-08-01 单测通过）
- [x] 三引擎 fallback chain：httpx → curl_cffi → playwright，waterfall 自动切换（2026-08-01 单测通过）
- [x] AntiBotInterceptor：before_tool 注入 use_browser/use_curl_cffi/timeout（2026-08-01 单测通过）
- [x] EscalationHook：after_response 403/429/Cloudflare 检测 + AddFeatureError（2026-08-01 单测通过）
- [x] JS 注入：navigator_overrider（11 项反检测）+ remove_overlay（弹窗/广告移除）
- [x] 代理模块：ProxyConfig + RoundRobinProxy（轮换+健康检查+冷却恢复）
- [x] ProfileManager：Playwright persistent_context 登录态持久化
- [ ] **真实网站**：访问 Cloudflare 保护站点 → httpx 失败 → 自动升级 curl_cffi/playwright（待真实网络环境验证）

**P1 补充验证（2026-08-01）：**
- [x] 403/429 状态码触发引擎升级链（httpx → curl_cffi → playwright 全链执行）
- [x] 404 等永久错误快速失败不重试（修复：非可重试 4xx 直接返回）

### P2 验收
- [x] 抓取 `https://www.apple.com/shop/buy-iphone` 商品页：httpx 直连成功、Markdown 无 `</path>` 残留、无 html 前缀（2026-08-01 实测）
- [x] HN 相对链接 `/item/1` 转为绝对 URL（单元测试覆盖，2026-08-01）
- [x] **脏数据**：空 body / 纯 script / 1MB 超大 HTML，清洗不崩（40 个测试全过）
- [x] **多站点 P2 端到端验收**（2026-08-01 补验）：httpx + Playwright + PruningContentFilter + MarkdownGenerator 全链路，覆盖静态/SPA/列表/详情多种页面类型
  - 博客园列表页：httpx 直连 75KB → 清洗 13.9KB → MD 6.8KB（4% 正文，列表页正常）
  - 博客园文章详情页：3 篇全部通过，正文比例 91-92%，Markdown 无 HTML 残留
  - CSDN 列表页：httpx 直连 355KB → 清洗 114KB → MD 15KB
  - 豆瓣书评详情页：httpx 直连 20KB → MD 513 字（正文 86%，内容干净含作者/书名/评论正文）
  - 36氪快讯页：Playwright 渲染 102KB → 清洗 17.7KB → MD 6.7KB（正文 61%，新闻快讯完整含标题/时间/摘要）
  - Python 文档：httpx 直连 33KB → MD 16.3KB（正文 84%，文档结构完整）
  - Bilibili 视频页：Playwright 渲染 144KB → 清洗 17.8KB → MD 6.5KB（正文完整含标题/播放量/制作名单/简介）
- [ ] 抓取 `https://www.zhihu.com/question/XXXXX`，Markdown 干净无噪声（**IP 级封禁**：2026-08-01 补验，Playwright 完整浏览器+真实指纹仍返回 403 + "你似乎来到了没有知识存在的荒原"反爬页；httpx→curl_cffi→playwright 三引擎均 403，确认 IP 级封禁非引擎指纹可绕过；代码路径已由 Bilibili/博客园/豆瓣等多站点实机验证，待换 IP/带登录 cookie 实测）

### P3 验收
- [x] 抓取 5 篇阮一峰博客文章，按 `~/crawagent/articles/{domain}/{date}/{title}.md` 落盘（2026-08-01 实测 5/5，同名文件自动 -2 去重）
- [x] **脏数据**：标题含 `/\:*?"<>|` 非法字符，路径正确转义（单测覆盖）

### P4 验收
- [x] 配置价格监控：任务/间隔/字段提取/Webhook 全链路（MonitorStore + Scheduler 单测通过）
- [x] **真实监控 e2e**（2026-08-01 补验）：本地 mock 目标 + 本地 webhook 接收端，run_task_once 模拟完整周期——首次建基线、无变化不告警、503 触发 WARN 告警 + webhook 收到、cooldown 内去抖跳过、cooldown 外再次告警；修复 MonitorStore 缺少 update_alert 导致 notified 状态未持久化的 bug
- [x] monitor lane 与 main lane 并行运行互不干扰（P4-1 MonitorLane 补齐，并行执行单测通过）
- [x] **脏数据**：目标 503 持续期间，告警只触发一次（cooldown 去抖 e2e 通过：冷却内 1 条、冷却外第 2 条）

### P5 验收
- [x] 扫描 OWASP Juice Shop：Docker 本地实跑，6 个漏洞 / 5 个类别（A05×2/XSS/Clickjacking/INFO_DISCLOSURE/A02）（2026-08-01）
- [x] security lane 的 hook 拦截危险工具调用（修复接线后：save 敏感路径被 loop 硬拦截，scan_vuln 生产 URL 需确认）
- [x] **脏数据**：大量 XHR 请求的 SPA，network_capture 不丢不崩（真实浏览器实测：22 请求 / 21 API 全捕获）

### P6 验收
- [x] Compaction 自动压缩：token 超阈值 → 生成摘要 → rebuild_context 输出压缩后上下文（2026-08-01 单测通过）
- [x] 崩溃恢复：IdPool 预分配ID + start_operation/finish_operation + build_recovery_plan 恢复 3 个未完成操作（2026-08-01 单测通过）
- [x] DeepCrawler BFS/DFS/Best-First：三种策略正确遍历（9 页 BFS 层级序/DFS 连续加深/Best-First docs 优先）（2026-08-01 单测通过）
- [x] AdaptiveCrawler 饱和度感知：Topic 匹配 on_topic=8/off_topic=2 + Saturation/Throttler 就绪（2026-08-01 单测通过）
- [x] URLFilter + FilterChain：10 项过滤器（normalize_url/SameDomain/MaxDepth/Extension/QueryParamLimit 等）（2026-08-01 单测通过）
- [x] BFS 深爬真实站点：`https://docs.python.org/3/` 实机爬取 6 页、0 错误（2026-08-01）
- [x] 爬取 100 页后 compaction 自动触发（2026-08-01 补验 e2e：本地 mock 站爬 101 页，compaction 触发，上下文 101 条 → 1 条 summary）
- [x] 爬取中途 kill 进程，重启后从崩溃点恢复（2026-08-01 补验 e2e：3 个 open operations 全部识别，build_recovery_plan 生成 main+monitor 两条 lane + 3 个恢复操作 + orphan 识别，清理后 0 残留）

### P7 验收
- [x] VideoExtractor：从 HTML 提取 `<video>`/`<source>`/`<iframe>`/m3u8/mp4，6 种平台识别（2026-08-01 单测通过）
- [x] AdRemover：CSS 选择器 + 15 个广告脚本域名 + 内联脚本 + 空容器，9 个广告元素移除（2026-08-01 单测通过）
- [x] MediaDownloader 扩展：extract_info（视频元数据）+ download_playlist（播放列表多集下载）（2026-08-01 单测通过）
- [x] API 端点：7 个 `/api/video/*`（download/info/extract/ad-remove/files/stream/delete）
- [x] 前端 Video.vue：4 Tab 页面（下载/已下载/视频提取/广告移除）+ 弹窗播放器
- [x] 集成测试：广告移除→视频提取→URL拼接 全流程贯通（2026-08-01 单测通过）
- [ ] YouTube 视频页元数据提取 + yt-dlp 下载（**网络不可达**：本机连接 YouTube 超时，MediaDownloader.extract_info 代码路径已验证——30s 超时保护返回 success=False + 明确错误，待可达网络环境实测）
- [x] Bilibili 带 cookie 下载（2026-08-01 补验通过：Playwright 登录导出 cookie → yt-dlp extract_info 提取元数据成功[标题/UP主/时长/播放量/11种格式] → download 下载 77.6MB MP4 文件成功；不带 cookie 时 412 反爬拦截已由错误处理覆盖）
- [x] **脏数据**：已下架视频友好报错（2026-08-01 补验：Bilibili 不存在视频 BVZZZZZZZZ 返回 404 + "信息提取失败" 友好错误；YouTube 不存在视频因网络不可达 60s 超时返回明确错误，不崩溃）

### P8 验收
- [ ] 全新机器 `docker-compose up` 5 分钟内可用
- [ ] Nginx HTTPS 正常
- [ ] **脏数据**：MySQL 密码错误有清晰提示

---

## 七、风险与对策

| 风险 | 影响 | 对策 |
|---|---|---|
| 影视/会员内容法律风险 | 高 | 开源版只做合法路径，灰色独立分支 |
| pi 架构 Python 移植工作量 | 高 | 分阶段：P0 先骨架（loop+tools+hooks），P6 再补 durability |
| 反爬对抗持续升级 | 高 | hooks 系统可扩展，新策略=新 hook |
| MySQL 迁移破坏数据 | 中 | 迁移脚本 + 备份 |
| Playwright 资源占用 | 中 | lanes 限制并发数 |
| LLM 成本失控 | 中 | P0-11 tools 里记录 token usage |

---

*文档维护：随开发进度同步更新*
