# CrawAgent 可改进优化文档

> 目标读者：项目开发者 / 维护者
> 定位：基于工程评审结论，将"已合格但仍有差距"的点转化为可执行改进路线
> 更新：2026-08-02

---

## 1. 现状结论

CrawAgent 已达到**合格工程基线**：架构对齐 pi Agent Harness、缓存前缀稳定（实测命中率趋向 99%）、selectolax 原生崩溃已根治（改 lxml）、84 项测试通过、前端构建与端到端爬取稳定。

但对照参考项目（pi / firecrawl / crawl4ai / Reasonix），仍有 6 项差距，本文档逐项给出**现状 → 目标 → 实施步骤 → 验证方式 → 优先级**。

---

## 2. 改进项总览

| # | 改进项 | 优先级 | 工作量 | 状态 |
|---|--------|--------|--------|------|
| 1 | 双轨合一（废弃 LangGraph 旧路径） | P0 | 中 | ✅ 已完成 |
| 2 | deep_crawl 引擎接入 Agent 工具链 | P0 | 小 | ✅ 已完成 |
| 3 | 监控 / 安全 Lane 能力强化 | P1 | 中 | ✅ 已完成 |
| 4 | 重试队列与断点续抓 | P1 | 中 | ✅ 已完成 |
| 5 | 测试覆盖提升 | P1 | 中 | ✅ 已完成 |
| 6 | 部署与运维补全 | P2 | 中 | ✅ 已完成 |

---

## 3. 改进项详情

### 3.1 双轨合一：废弃 LangGraph 旧路径

**现状**
- 新旧两条执行路径并存：
  - 旧路径：`/api/agent/run` → `crawagent/graph/agent_workflow.py`（LangGraph 五节点工作流）
  - 新路径：`/api/harness/prompt` → `crawagent/harness/`（pi 风格 Loop）
- 前端 Chat 页已走 harness 新路径；但旧路径 API 仍对外暴露。
- 风险：两条路径行为不一致（规划参数、工具执行、token 统计），维护成本翻倍，缓存优化只在新路径生效。

**目标**
- 收敛为单一执行路径（harness），旧路径下线或降级为兼容转发。
- 保留 `/api/agent/*` 接口签名，内部转发到 harness，避免破坏既有调用方。

**实施步骤**
1. 盘点旧路径独有能力：`graph/agent_workflow.py` 中的规划节点、`graph/anti_bot.py` 反爬编排，确认其逻辑是否已在新路径中覆盖（supervisor 已覆盖反爬升级，规划已由 task_notes 注入）。
2. 将 `/api/agent/run` 处理器改为调用 `harness.prompt()`，任务状态/进度字段映射到旧响应格式。
3. 前端 `agentApi` 全部切换为 `harnessApi`；删除 `_run_agent_background` 后台任务逻辑或改为薄封装。
4. 确认后删除 `graph/agent_workflow.py` 与 `graph/site_analyzer.py` 中未引用的部分（site_analyzer 的 `analyze_site_simple` 被 analyze_executor 使用，需保留迁移）。

**涉及文件**
- `crawagent/api/server.py`（/api/agent/run 端点）
- `crawagent/graph/agent_workflow.py`、`crawagent/graph/anti_bot.py`
- `frontend/src/api/index.js`、`frontend/src/views/Tasks.vue`
- `tests/`（新增双轨一致性测试）

**验证方式**
- 旧接口 `/api/agent/run` 与 `/api/harness/prompt` 对同一任务返回一致的爬取结果。
- 全量 pytest + vite build 通过；端到端：Tasks 页发起任务正常。

**优先级**：P0（先做，消除双轨风险）

---

### 3.2 deep_crawl 引擎接入 Agent 工具链

**现状**
- `crawagent/core/deep_crawl.py` 已实现完整引擎：`DeepCrawlStrategy`（BFS/DFS/Best-First）、`DeepCrawler` 调度器、`CrawledPage` 产物、`DeepCrawlStats` 统计，并集成了 Fetcher + FilterChain。
- 但 `crawagent/harness/tools.py` 中**无任何 deep_crawl 引用**，引擎从未被 Agent/supervisor 调用，处于"有引擎无入口"状态。
- 测试 `tests/test_deep_crawl.py` 存在，说明引擎单测通过。

**目标**
- 将深爬能力暴露为 Agent 可用工具：新增 `deep_crawl` 工具定义 + 执行器，注册到工具注册表。
- supervisor 或 Agent 收到"整站/多页深爬"指令时自动走深爬引擎。

**实施步骤**
1. 在 `crawagent/harness/tools.py` 新增 `DEEP_CRAWL_TOOL` 定义（参数：url、strategy、max_pages、max_depth、allowed_domains）与 `deep_crawl_executor`（调用 `DeepCrawler.run()`，返回 `DeepCrawlStats` + 页面产物摘要）。
2. 注册到 `create_default_tools()`，并加入 `LLM_CALLABLE_TOOLS`（若希望 Agent 直接调用）。
3. supervisor 增加策略分支：当 `max_pages > 单页阈值`（如 >1）时，先 deep_crawl 收集原始页面产物，再对每个页面走 analyze→extract→save。
4. 深爬产物接入 save_executor（批量保存 CrawledPage 的 markdown/结构化结果）。

**涉及文件**
- `crawagent/harness/tools.py`
- `crawagent/core/deep_crawl.py`（如需补充产物序列化）
- `crawagent/harness/system_prompt.py`（工具描述）

**验证方式**
- 端到端：指令"深度抓取 xx 网站前 20 个页面"，断言深爬执行、页面产物批量保存、token 统计累计。
- 单测：deep_crawl_executor 对 mock Fetcher 的输入输出。

**优先级**：P0（紧接双轨合一后）

---

### 3.3 监控 / 安全 Lane 能力强化

**现状**
- `crawagent/monitor/`：baseline / diff_detector / notifier / scheduler 均已实现，监控 Lane 可创建任务、定时检查、Webhook 通知。
- `crawagent/security/`：vuln_scanner / security_lane / auto_fixer 已实现基础扫描与修复建议。
- 与参考项目相比功能单薄：监控缺"变化幅度量化"（当前是文本 diff，无结构化字段级 diff）；安全缺漏洞等级细化与自动修复闭环。

**目标**
- 监控：字段级 diff（价格/标题变化可量化，参考 crawl4ai `watch` 系列）。
- 安全：扫描结果等级化（高危/中危/低危）+ 可配置自动修复策略。

**实施步骤**
1. 监控 `diff_detector.py`：在文本 diff 基础上，对结构化字段（JSON/表格）做字段级对比，输出 `{field: old_value, new_value, changed}` 结构。
2. 监控 `notifier.py`：支持按"字段变化幅度"过滤通知（如价格降幅 > 5% 才通知）。
3. 安全 `vuln_scanner.py`：扫描项增加 `severity` 字段；`security_lane` 输出按等级分组的报告。
4. 安全 `auto_fixer.py`：修复项按等级白名单，高危项默认只报告不自动执行。

**涉及文件**
- `crawagent/monitor/diff_detector.py`、`notifier.py`、`monitor_lane.py`
- `crawagent/security/vuln_scanner.py`、`security_lane.py`、`auto_fixer.py`
- `frontend/src/views/Monitor.vue`、`Security.vue`

**验证方式**
- 单测：字段级 diff 输出结构正确；severity 分级正确。
- 前端：监控页展示字段变化明细；安全页按等级分组。

**优先级**：P1

---

### 3.4 重试队列与断点续抓

**现状**
- `crawagent/core/frontier.py` 提供任务队列与重试计数，但**未接入 harness 执行路径**。
- 单次任务失败后无自动重试（除 extract 的 NEED_REANALYSIS 内部重试与浏览器升级兜底）。
- 无断点续抓：大任务中途失败需整体重跑。

**目标**
- 大任务（deep_crawl / 批量列表）支持失败重试与断点续抓。
- 失败 URL 进入重试队列，恢复后可从未完成处继续。

**实施步骤**
1. frontier 增加"任务快照"能力：记录已抓 URL 集合与未完成 URL 集合（SQLite 已有表，补字段）。
2. supervisor 集成：每次批量抓取后把失败 URL 写回 frontier，标记 `retry_count+1`。
3. 恢复入口：`/api/agent/resume` 或 harness prompt 附带 `resume_token`，从快照继续。
4. 图片下载失败（GAP-005）复用同一重试机制：失败图片 URL 入队，重试 1-2 次。

**涉及文件**
- `crawagent/core/frontier.py`
- `crawagent/harness/tools.py`（supervisor 集成）
- `crawagent/api/server.py`（resume 端点）

**验证方式**
- 模拟中途失败（mock Fetcher 对某 URL 抛错），断言失败 URL 入队、恢复后续抓成功。
- 单测：frontier 快照读写。

**优先级**：P1

---

### 3.5 测试覆盖提升

**现状**
- 84 项测试（pytest），覆盖：chunking / content_filter / extractor / markdown / monitor / output / prompts_usage / security / video / deep_crawl。
- 缺口：无 harness Loop 完整链路集成测试（FakeSession 只覆盖局部）；无前端 E2E；无 LLM mock 全链路。

**目标**
- 补齐 harness 集成测试（loop + supervisor + save 全链路，MockLLM 驱动）。
- 前端补 1-2 个关键视图冒烟测试（Chat 发送、历史加载）。

**实施步骤**
1. 复用 `crawagent.llm.mock.MockLLM` 编写 `test_harness_e2e.py`：
   - 创建 session → prompt（爬取指令）→ 断言消息树、tool 结果、token 统计、usage 字段。
   - 覆盖：超限触发 compaction、崩溃恢复、task_notes 注入、force_tool_retry 追加消息不改前缀。
2. `test_cache_stability.py`：断言同一 session 内 system prompt 字节一致（构造两次 loop 比较 `_system_prompt`）。
3. 前端（可选）：Vitest 冒烟——Chat.vue 渲染 + mock api 响应。

**涉及文件**
- 新增 `tests/test_harness_e2e.py`、`tests/test_cache_stability.py`
- `tests/conftest.py`（共享 fixture）

**验证方式**
- pytest 全量通过；覆盖率报告（`pytest --cov`）harness 模块 ≥60%。

**优先级**：P1（随 3.1/3.2 改动同步补测试）

---

### 3.6 部署与运维补全

**现状**
- `docker-compose.yml` 已编排 mysql + redis，`docker/mysql/init.sql` 提供建表与迁移 SQL。
- 缺失：后端/前端应用服务编排、一键启动脚本、健康检查、日志持久化、部署文档（不含敏感信息）。

**目标**
- `docker-compose up` 一键拉起 后端(uvicorn) + 前端(nginx 静态托管) + mysql + redis。
- 健康检查联动；日志落盘；提供部署文档模板（凭据占位，不写真实密钥）。

**实施步骤**
1. 新增 `docker/Dockerfile.backend`（python:3.13-slim + .venv 依赖 + `main.py serve`）与 `docker/Dockerfile.frontend`（node 构建 + nginx 托管 dist）。
2. `docker-compose.yml` 增加 backend/frontend 服务，依赖 mysql/redis 健康检查通过后启动。
3. 新增 `docker/nginx.conf`（反向代理 /api → backend:8000，静态托管前端）。
4. 新增 `docs/DEPLOYMENT.md`：环境变量清单（全部占位符）、一键启动步骤、常见问题；**不包含任何真实密钥/证书细节**（按项目安全约定）。
5. `.env.example` 补充应用侧变量（模型名、超时、harness 参数）。

**涉及文件**
- `docker/Dockerfile.backend`、`docker/Dockerfile.frontend`、`docker/nginx.conf`
- `docker-compose.yml`（新增服务）
- `docs/DEPLOYMENT.md`、`.env.example`

**验证方式**
- 本机 `docker compose up -d` 全服务健康；前端页面可访问；爬取任务经 nginx 转发后端成功。

**优先级**：P2

---

## 4. 建议实施顺序

```
P0 阶段（稳定性收敛）
  ├─ 3.1 双轨合一          （消除双路径风险）
  └─ 3.2 deep_crawl 接入    （补齐整站抓取入口）
        ↓
P1 阶段（能力补强）
  ├─ 3.5 测试覆盖           （与 3.1/3.2 同步，锁住回归）
  ├─ 3.3 监控/安全 lane 强化
  └─ 3.4 重试队列/断点续抓
        ↓
P2 阶段（交付完善）
  └─ 3.6 部署与运维
```

每完成一项：更新本文档状态列（未开始 → 进行中 → 已完成），并在 KNOWN_GAPS.md 对应条目标注修复状态。

---

## 5. 与既有文档的关系

- **KNOWN_GAPS.md**：记录开发过程中发现的缺陷（bug 级），本文档聚焦"工程能力差距"（架构级）。
- **DEVELOPMENT_PLAN.md**：历史开发计划（P0-P8），本文档是 P8 之后的持续改进路线。
- 本改进项落地时，若发现新的 bug 级缺口，照例追加到 KNOWN_GAPS.md。
