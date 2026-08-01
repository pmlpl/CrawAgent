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

### 13. P3 阶段完成（2026-08-01，含实机验收）

**文件整理到指定路径：**
- `crawagent/output/organizer.py`：FileOrganizer 路径模板引擎（{domain}/{date}/{year}/{month}/{day}/{title}/{slug}/{ext}）+ Windows 非法字符转义 + 同名去重
- `crawagent/output/media_downloader.py`：MediaDownloader（yt-dlp 视频 + httpx 流式下载，支持断点续传/Range/416）
- `harness/tools.py`：save 工具支持路径模板，Supervisor 保存阶段改用 `articles/{domain}/{date}/{title}.{ext}` 模板
- `frontend/views/Output.vue` + `/output` 路由：模板预览 + 文件列表 + 查看/删除
- `api/server.py`：`/api/output/files` / `preview-path` / `file`(GET/DELETE)

**验收实测（2026-08-01）：**
- 阮一峰博客 5/5 篇文章抓取并落盘为 `articles/ruanyifeng.com/2026-08-01/{title}.md`（每篇约 19-20KB）
- 标题非法字符全部转义；同名文件自动追加 -2 后缀
- httpx 流式下载实机通过（断点续传逻辑单测覆盖）
- 测试 52 个全部通过（新增 11 个 P3 测试）

**检查中修复的问题：**
- `clean_result` 未定义导致 Supervisor 保存阶段每次 NameError 崩溃 → 改为从 result/crawl_result 取值
- `.gitignore` 的 `output/` 误吞 `crawagent/output/` 包（P3 代码无法提交）→ 改为根锚定 `/output/`
- `/api/output/file` 路径越权检查用 startswith 可被 `output_evil` 前缀绕过 → 改用 Path.relative_to
- `~/` 开头的路径模板被错误拼到 base_dir 下 → 修复 render 判断

### 14. P4-P7 验收与修复（2026-08-01）

**P4 监控（验收通过）：**
- MonitorStore / DiffDetector / Scheduler / Notifier / Baseline 全链路单测通过
- 告警去抖：503 持续失败 cooldown 内只告警一次（验收标准达成）
- P4-1 MonitorLane 补齐（原缺失）：与 main lane 并行执行监控任务，绑定 harness session 自动建 lane
- apscheduler 加入依赖并安装（调度器实际可用）

**P5 安全（验收通过）：**
- VulnScanner 本地测试站实测 3+ 漏洞、多类别（A05/INFO_DISCLOSURE/XSS）
- SecurityHook 修复：原实现从未被注册且上下文格式与 loop 不兼容（before_tool 读 tool_name，
  loop 传 tool_calls）→ 重写为 loop 格式 + `create_security_hooks` 接线 + loop 硬拦截
  （save 敏感路径直接拒绝执行；scan_vuln 生产 URL 标记需确认）
- 修复 create_security_hooks 缩进错乱导致 after_tool 成为死代码的问题
- network_capture 真实浏览器实测：SPA 22 个请求 / 21 个 API 全部捕获，不丢不崩
- 工具暴露：LLM_CALLABLE_TOOLS 加入 monitor/check_change/scan_vuln/fix_issue

**P6 压缩/持久化/深爬（验收通过）：**
- Compaction：estimate/should_compact/compact 全流程验证（含 LLM 失败回退摘要）
- 崩溃恢复：MySQL start_operation → get_open_operations → finish_operation 验证通过
- DeepCrawler：BFS/DFS 本地站点单测 + docs.python.org 实机 BFS 爬取 6 页 0 错误
- FilterChain/Saturation/Adaptive 单测通过

**P7 影视（代码级通过，真实站点待网络）：**
- VideoExtractor 6 平台识别 + AdRemover + ProfileManager + MediaDownloader(yt-dlp/Content-Type 推断) 单测通过
- yt-dlp 已安装并加入依赖；YouTube/Bilibili 实机受本机网络限制待验证

**验收修复清单：**
1. SecurityHookHandler 未接线 + 上下文不兼容（重写 + 接线 + loop 硬拦截）
2. security_hook.py 缩进错乱（after_tool 死在 create_security_hooks 的 return 之后）
3. P4-1 monitor_lane.py 缺失（已实现 MonitorLane + 测试）
4. LLM_CALLABLE_TOOLS 缺 monitor/check_change/scan_vuln/fix_issue（已补）
5. Notifier webhook payload url 未回退 task.url（已补）
6. AdRemover 选择器补 `[class*='ad-']` / `[id*='ad-']`
7. loop._execute_single_tool 安全拦截检查提前到 tool 查找之前
8. pyproject 补 apscheduler / yt-dlp 依赖

**当前测试：79 个全部通过**（新增 P4 监控 10 + P5 安全 6 + P6 深爬 6 + P7 影视 7 + MonitorLane 2）

### 15. 真实网络补验（2026-08-01，切换网络后）

**P0 端到端（真实 LLM）：**
- DeepSeek API key 有效；`CrawlHarness.prompt` 全流程 completed（78.7s，5 turns）
- Supervisor crawl → clean → analyze → extract(confidence=90) → save 闭环跑通，第二次调用命中蓝图缓存
- 脏数据 6 项（空串/not-a-url/javascript:void(0)/https:// /localhost:9999/about:blank）全部优雅返回，不崩溃

**P1 引擎升级链修复：**
- 原回退链只在异常时升级，403/429 状态码不会切换引擎；Playwright/curl_cffi 把 404 也当可重试错误（浪费 ~25s×N）
- 修复：httpx/curl_cffi/playwright 三引擎对非可重试 4xx 快速失败，403/429 触发升级，5xx 重试
- 本地实测：404 0 秒失败、403 全链 1.1 秒、200 直连

**P2 知乎：** 本机 IP 被知乎屏蔽（httpx 与 Playwright 均 403），属环境限制，代码路径已由其他站点验证

**P5 Juice Shop 真实验收：**
- Docker 本地跑 OWASP Juice Shop，VulnScanner 实测 6 个漏洞 / 5 个类别（验收要求 ≥5 类）
- 为达到验收标准新增两项标准检查：Set-Cookie HttpOnly/SameSite（A07）、明文 HTTP 传输（A02）

**P7 YouTube/Bilibili：** 切换网络后仍被屏蔽（ConnectTimeout），维持"代码级通过，待可达网络"

**当前测试：80 个全部通过**

### 16. 剩余验收补跑（2026-08-01）

**P4 真实监控 e2e（验收通过）：**
- 本地 mock 目标 + 本地 webhook 接收端，run_task_once 模拟完整监控周期
- 首次建立基线无告警、无变化不告警、503 触发 WARN 告警 + webhook 收到
- cooldown 内 503 去抖跳过（debounce_ok=True）、cooldown 外再次告警（second_alert_after_cooldown=True）
- 连续失败 ≥3 次告警级别自动升 ERROR
- **修复 bug**：`MonitorStore` 缺少 `update_alert` 方法，导致 `_maybe_alert` 在 notify 后只更新内存中 `alert.notified`，store 中告警的 notified 字段永远为初始 False。修复：models.py 新增 `update_alert`（持久化 notified/notify_error）+ scheduler.py `_maybe_alert` 在 notify 后调用 `store.update_alert(alert)`
- 测试参数教训：503 触发 fetcher 5xx 重试链（httpx→curl_cffi→playwright + 指数退避）单次约 14s，cooldown 必须 > 连续两次抓取耗时之和才稳定去抖（原 3s 失败，改 45s 通过）。引擎重试链是禁止回退的修复项，不能动

**P6 完整演练 e2e（补验通过，未回归）：**
- 100 页爬取触发 compaction：本地 mock 站爬 101 页，compaction 自动触发，上下文 101 条 → 1 条 summary
- 崩溃恢复：3 个 open operations 全部识别，build_recovery_plan 生成 main+monitor 两条 lane + 3 个恢复操作 + orphan_entry_ids 识别，清理后 0 残留

**P7 影视实机（Bilibili 全面通过，YouTube 网络不可达）：**
- **Bilibili 全面通过**（2026-08-01 补验）：
  - P2 Markdown 清洗：Playwright 抓取 144KB HTML → PruningContentFilter 清洗至 17.8KB（压缩 12.4%）→ Markdown 6.5KB，内容完整（标题/播放量/发布时间/制作名单/视频简介/标签），无 HTML 残留
  - P7 VideoExtractor：提取到 1 个 video + 1 个 iframe（bilibili player）
  - P7 yt-dlp 元数据：Playwright 登录导出 cookie → extract_info 成功（标题/UP主/时长 313s/播放量 21290/11 种格式含 360p-1080p）
  - P7 视频下载：download 下载 77.6MB MP4 文件成功（yt-dlp + cookie）
  - P7 已下架视频：不存在 BVZZZZZZZZ 返回 404 + "信息提取失败" 友好错误，0.5s 未崩溃
- YouTube：本机连接超时（NET_UNREACHABLE），MediaDownloader.extract_info 代码路径已验证（30s 超时保护返回 success=False + 明确错误）

**知乎/P0 HN（网络环境限制，如实记录跳过）：**
- 知乎 zhihu.com/question：Playwright 完整浏览器+真实指纹仍 403（返回"你似乎来到了没有知识存在的荒原"反爬页），确认 IP 级封禁非引擎指纹可绕过；httpx→curl_cffi→playwright 三引擎均 403，引擎升级链在 403 上正确触发（验证 P1 修复）
- HN news.ycombinator.com：GFW DNS 污染（系统 DNS→122.10.85.4 污染 IP，阿里 DoH→185.45.6.57 真实 IP）+ IP 阻断（直连真实 IP 仍 ConnectError）
- 两者均为本机网络环境限制，非代码缺陷；代码路径已由 apple.com（P2）/Bilibili（P2+P7）/阮一峰（P3）/Juice Shop（P5）/docs.python.org（P6）等其他站点实机验证

**当前测试：80 个全部通过**（补验未引入回归）

---

## 当前状态

**P0-P7 全部完成 ✅，剩余验收补跑完成 ✅（Bilibili 全面通过；知乎/HN/YouTube 受本机网络限制待可达网络实测）**

- P0：CrawlHarness 骨架可运行（真实 LLM 端到端跑通）
- P1：引擎回退链 + AntiBot hooks + 时间精度修复完成（403/429 触发升级链已验证）
- P2：清洗/提取/分块/提示词/用量模块已实现，苹果官网实机验证通过；**Bilibili P2 Markdown 清洗补验通过**（知乎 IP 级封禁待换网络）
- P3：文件整理/媒体下载/输出 API/前端 Output 页完成，阮一峰 5 篇实机落盘验收通过
- P4：监控 + Lanes 完成，**真实监控 e2e 补验通过**（含告警通知状态持久化 bug 修复）
- P5：安全扫描完成，Juice Shop 6 漏洞/5 类别实测通过
- P6：压缩/持久化/深爬完成，**100 页 compaction + 崩溃恢复 e2e 补验通过**
- P7：影视内容 **Bilibili 全面通过**（P2 清洗 + P7 元数据/下载/404 友好报错）；YouTube 网络不可达（代码路径已验证）
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

### 待可达网络环境补验（本机网络限制，非代码缺陷）
1. **知乎验收** - 抓取 zhihu.com/question 出干净 Markdown（Playwright 完整浏览器仍 403，IP 级封禁，换网络/带 cookie 可试）
2. **P0 HN 端到端** - news.ycombinator.com（GFW DNS 污染 + IP 阻断，需代理）
3. **P7 YouTube** - YouTube 元数据 + yt-dlp 下载（GFW 封锁，需代理）
4. **LLM 过滤器验证** - LLMContentFilter 用真实 key 跑通（需更换失效的 DeepSeek API key）

### 中优先级（功能增强）
5. **WebSocket 支持** - Agent 执行时实时推送日志到前端（替代轮询）
6. **任务持久化** - 当前任务结果存在内存，需持久化到 MySQL
7. **frontier.py SQLite → MySQL 迁移** - 统一数据库
8. **任务队列隔离** - 当前所有任务共用一个 frontier 数据库，需按 job_id 隔离
9. **GAP-001** - html2text 处理 HN 链接 baseurl 拼接错误（生成非法 URL，不影响验收）
10. **GAP-003** - save_executor 图片下载为串行（批量抓取时耗时增加，引入 Semaphore 并发）

### 低优先级
11. **P8 Docker + Nginx 部署** - 本次任务暂缓（用户要求不做部署）
12. 登录/Cookie 注入、签名接口逆向、验证码处理（灰色路径，独立分支）

---

## 已知问题

1. **DeepSeek API key 失效（401）** - 需更换有效 key
2. **frontier.py 仍使用 SQLite** - 待迁移到 MySQL
3. ~~monitor / scan_vuln 执行器仍为空~~ - ✅ 已在 P4/P5 完成（MonitorScheduler + VulnScanner + SecurityLane）
4. **LLMContentFilter 未用真实 key 验证** - 单测只覆盖非 LLM 路径
5. `crawagent/graph/site_analyzer.py` 依赖 Playwright，需要 `playwright install chromium`
6. LLM 每次生成的 CSS 选择器不一致，有时不准
7. 系统代理问题：必须通过 `main.py` 启动才能正确禁用代理
8. 多个任务共用同一个 frontier 数据库，可能互相干扰
9. **GAP-001**：html2text 处理 HN 链接 baseurl 拼接错误（生成非法 URL，不影响验收）
10. **P4 监控告警通知状态**：✅ 已修复（2026-08-01 补验发现并修复 update_alert 缺失）
11. **网络环境限制**：知乎(403)/HN(ConnectTimeout)/YouTube(超时)/Bilibili(412) 本机不可达，代码路径已由其他可达站点验证，待换网络补验

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
