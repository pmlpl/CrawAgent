# CrawAgent 架构设计文档

> 版本：v2.1 | 更新：2026-08-01
> 变更：新增引擎抽象层（engines/）、视频提取/广告移除（extractors/）、登录态持久化（sessions/）、反爬Hook（antibot_hook/escalation_hook）、深度爬取/饱和度感知/URL过滤器、代理轮换

---

## 1. 总体架构

```
┌─────────────────────────────────────────────────────────────────────────┐
│                            用户层                                       │
│   CLI (Click)  /  Vue 3 Web UI  /  API Client                          │
└────────────────────────────────┬────────────────────────────────────────┘
                                 │
                                 ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                          API 网关层                                     │
│   FastAPI + Uvicorn                                                     │
│   /api/harness/* (新)   /agent/run   /agent/analyze   /crawl/direct    │
│   /health                                                               │
└────────────────────────────────┬────────────────────────────────────────┘
                                 │
                                 ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                    Agent Harness 编排层                                  │
│                                                                         │
│   ┌───────────────────────────────────────────────────────────────────┐ │
│   │  CrawlHarness  (harness/harness.py)                              │ │
│   │  phase: idle → turn → compact                                   │ │
│   │  prompt() / quick_crawl()  入口                                 │ │
│   │  session / lane / tool / hook 管理                              │ │
│   └───────────────────────┬───────────────────────────────────────────┘ │
│                           │                                             │
│   ┌───────────────────────▼───────────────────────────────────────────┐ │
│   │  CrawlLoop  (harness/loop.py)                                    │ │
│   │  driverLoop:                                                     │ │
│   │  checkpoint → step(LLM) → execute_tool_batch(三阶段)             │ │
│   │  → followUp → finish                                             │ │
│   └───────────────────────────────────────────────────────────────────┘ │
│                                                                         │
│   ┌──────────────┐ ┌──────────────┐ ┌──────────────┐ ┌──────────────┐  │
│   │ CrawlSession │ │  CrawlHooks  │ │  CrawlEnv    │ │  Compaction  │  │
│   │ (MySQL持久化) │ │  (8种事件)   │ │ (执行环境)   │ │ (上下文压缩) │  │
│   └──────────────┘ └──────────────┘ └──────────────┘ └──────────────┘  │
│                                                                         │
│   ┌──────────────────────┐ ┌────────────────────────────────────────┐   │
│   │ SystemPromptAssembler│ │  ToolRegistry (7 内置工具)             │   │
│   │ (四段式组装)          │ │  crawl/extract/analyze/save/          │   │
│   │                      │ │  search/monitor/scan_vuln             │   │
│   └──────────────────────┘ └────────────────────────────────────────┘   │
└────────────────────────────────┬────────────────────────────────────────┘
                                 │ Tool Call
                                 ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                         工具执行层                                       │
│   7 个内置工具（纯代码实现，由 LLM tool calling 驱动调度）               │
│                                                                         │
│   ┌────────┐ ┌─────────┐ ┌─────────┐ ┌──────┐ ┌────────┐              │
│   │ crawl  │ │ extract │ │ analyze │ │ save │ │ search │              │
│   └────────┘ └─────────┘ └─────────┘ └──────┘ └────────┘              │
│   ┌─────────┐ ┌───────────┐                                          │
│   │ monitor │ │ scan_vuln │   ← 可扩展：注册 CrawlToolDef + executor  │
│   └─────────┘ └───────────┘                                          │
│                                                                         │
│   引擎抽象层（engines/）：                                              │
│   ┌─────────────────────────────────────────────────────────────────┐  │
│   │  FallbackChain (waterfall)                                      │  │
│   │  ┌──────────┐ → ┌──────────────┐ → ┌──────────────┐            │  │
│   │  │ HttpxEng │   │ CurlCffiEng  │   │ PlaywrightEng│            │  │
│   │  │ (最快)   │   │ (TLS指纹绕过) │   │ (JS渲染+注入) │            │  │
│   │  └──────────┘   └──────────────┘   └──────────────┘            │  │
│   └─────────────────────────────────────────────────────────────────┘  │
│                                                                         │
│   提取器层（extractors/）：                                             │
│   ┌──────────────┐ ┌────────────┐                                      │
│   │VideoExtractor│ │ AdRemover  │                                      │
│   └──────────────┘ └────────────┘                                      │
│                                                                         │
│   旧模块（兼容保留）：                                                   │
│   Fetcher / Frontier / Extractor / SiteAnalyzer / AntiBot              │
└────────────────────────────────┬────────────────────────────────────────┘
                                 │
                                 ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                      存储 / 基础设施层                                   │
│   MySQL 8.0 (entries/operation_logs)  +  Redis 7 (缓存/限速)           │
│   文件系统 (爬取结果/视频下载)  +  Docker (容器化部署)                   │
│   代理池 (core/proxy.py)  +  登录态持久化 (sessions/profile_manager.py) │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 2. 核心模块设计

### 2.1 CrawlHarness（编排层主类）

**文件**：`crawagent/harness/harness.py`

**职责**：Agent Harness 编排层的顶层入口，管理完整的爬取任务生命周期。

**核心概念**：
- **Phase 管理**：`idle → turn → compact`，控制会话当前阶段
- **Session 管理**：创建 / 恢复 / 列举 CrawlSession
- **Lane 管理**：多并行会话通道，每个 lane 持有命名位置与 leaf 指针
- **Tool 管理**：通过 ToolRegistry 注册与调度工具
- **Hook 管理**：通过 CrawlHooks 注册与执行钩子

**入口方法**：
- `prompt(user_input, **kwargs)` → `RunResult`：通用任务入口，由 LLM 自主决定调用哪些工具
- `quick_crawl(url, **kwargs)` → `RunResult`：快捷爬取入口，自动编排 crawl → extract → save 流程

---

### 2.2 CrawlLoop（驱动循环）

**文件**：`crawagent/harness/loop.py`

**职责**：实现 driverLoop，是 Harness 的核心执行循环。

**循环流程**：
```
checkpoint（恢复上下文）
    → step（LLM 调用，获取 tool_calls）
    → execute_tool_batch（三阶段执行）
        ├─ 阶段 1：预执行（hook before_tool + 参数校验）
        ├─ 阶段 2：执行（调用工具 executor）
        └─ 阶段 3：后处理（hook after_tool + 结果记录）
    → followUp（LLM 判断是否继续）
    → finish / continue
```

**关键特性**：
- 每次 step 产生一个 **Turn Snapshot**（不可变）
- 工具批量执行支持并行与串行模式
- 循环终止条件：LLM 不再产出 tool_calls / 达到 max_turns / 用户取消

---

### 2.3 CrawlSession（持久化会话）

**文件**：`crawagent/harness/session.py`

**职责**：MySQL 持久化的会话管理，支持崩溃恢复与多 lane 并行。

**核心数据结构**：
- **对话树（entries）**：append-only，每条 entry 记录 role / content / tool_calls / tool_results，不可修改只可追加
- **Lanes**：命名位置 + leaf 指针，支持同一会话内多并行分支
- **Operation Logs**：持久化操作记录，预分配 ID，用于崩溃恢复（`get_open_operations` 找到未完成操作并继续执行）
- **Global Facts**：最新写胜（last-write-wins）的键值事实表，用于跨 turn 共享状态

**关键方法**：
- `create_lane(name)` - 创建新 lane
- `append_entry(entry)` - 追加对话条目
- `get_open_operations()` - 获取未完成操作（崩溃恢复入口）
- `set_fact(key, value)` / `get_fact(key)` - 全局事实读写

---

### 2.4 CrawlHooks（钩子系统）

**文件**：`crawagent/harness/hooks.py`

**职责**：提供 8 种 hook 事件的注册与链式执行机制。

**8 种 Hook 事件**：

| 事件 | 触发时机 | 典型用途 |
|------|----------|----------|
| `before_run` | CrawlLoop 每轮循环开始前 | 注入上下文、条件终止 |
| `before_tool` | 工具执行前 | 参数改写、权限校验 |
| `after_tool` | 工具执行后 | 结果改写、日志记录 |
| `transform_context` | 上下文组装后、发送 LLM 前 | 上下文裁剪、注入指令 |
| `before_request` | LLM 请求发送前 | 请求改写、限流 |
| `after_response` | LLM 响应返回后 | 响应改写、内容过滤 |
| `before_compaction` | 上下文压缩前 | 自定义压缩策略 |
| `before_run_end` | CrawlLoop 结束前 | 结果汇总、资源清理 |

**执行机制**：
- 每个 hook 支持多个回调，按优先级排序
- 链式执行：前一个回调的输出作为下一个的输入
- **Hooks 先于效果**：hook 回调在副作用发生前执行，可以拦截和修改

---

### 2.5 CrawlEnv（执行环境）

**文件**：`crawagent/harness/env.py`

**职责**：抽象执行环境，统一管理 HTTP 客户端、浏览器引擎、文件系统等资源。

**支持的引擎**：
- `httpx` - 主力 HTTP 客户端
- `curl_cffi` - 反爬场景（impersonate）
- `Playwright` - 动态渲染 / JS 交互
- 文件系统 - 结果持久化

**资源生命周期**：统一 `__aenter__` / `__aexit__` 管理，随 Harness 生命周期自动清理。

---

### 2.6 Compaction（上下文压缩）

**文件**：`crawagent/harness/compaction.py`

**职责**：当对话 token 数超限时，自动进行上下文压缩。

**策略**：
- 保留 system prompt + 最近 N 轮完整对话
- 对更早的对话轮次生成摘要（LLM 辅助）
- 保留 global_facts 不压缩
- 压缩发生在 phase 切换为 `compact` 时

---

### 2.7 SystemPromptAssembler（提示词组装）

**文件**：`crawagent/harness/system_prompt.py`

**职责**：四段式组装系统提示词。

**四段结构**：
1. **Core** - 角色定义 + 基本行为规则
2. **Site Feature** - 当前站点特征（由 SiteAnalyzer 提供）
3. **Task Notes** - 任务特定指令（由用户输入解析）
4. **User Append** - 用户额外追加的指令

**组装时机**：每次 LLM 调用前重新组装，确保上下文最新。

---

### 2.8 ToolRegistry（工具注册表）

**文件**：`crawagent/harness/tools.py`

**职责**：管理 7 个内置工具的定义与执行器注册。

**7 个内置工具**：

| 工具 | 用途 | exec_mode |
|------|------|-----------|
| `crawl` | 抓取 URL 内容 | async |
| `extract` | 从 HTML 提取结构化数据 | async |
| `analyze` | 站点结构分析 | async |
| `save` | 保存提取结果 | sync |
| `search` | 全文检索已保存数据 | sync |
| `monitor` | 监控页面变化 | async |
| `scan_vuln` | 扫描页面敏感信息 | async |

**注册方式**：`CrawlToolDef` 定义工具元信息 + executor 函数实现具体逻辑。

---

### 2.9 核心类型定义

**文件**：`crawagent/harness/types.py`

**CrawlToolDef**：工具定义类型
```python
@dataclass
class CrawlToolDef:
    name: str              # 工具名称
    description: str       # 工具描述（供 LLM 理解）
    parameters: dict       # JSON Schema 参数定义
    exec_mode: str         # "sync" | "async"
    replay_safe: bool      # 崩溃恢复时是否可重放
```

**RunResult**：判别联合，4 种 kind
```python
@dataclass
class RunResult:
    kind: Literal["completed", "needs_input", "error", "cancelled"]
    data: Any              # completed: 最终结果 / needs_input: 提问内容
    error: Optional[str]   # error 时的错误信息
    session_id: str        # 所属会话 ID
```

---

### 2.10 旧模块（兼容保留）

| 模块 | 文件 | 简要说明 |
|------|------|----------|
| Fetcher | `core/fetcher.py` | httpx + curl_cffi 双引擎抓取，指数退避重试，域名级限速 |
| Frontier | `core/frontier.py` | URL 队列管理（优先级 + 去重），待迁移至 MySQL |
| Extractor | `core/extractor.py` | 三级回退提取（selectolax → lxml → LLM） |
| SiteAnalyzer | `graph/site_analyzer.py` | Playwright + LLM 逆向站点接口 |
| AntiBot | `graph/anti_bot.py` | 策略升级链（httpx → curl_cffi → Playwright → 验证码 → 人工） |

---

## 3. 数据流向

### 3.1 爬取任务
```
用户输入
  → CrawlHarness.prompt()
    → 创建/恢复 CrawlSession
    → SystemPromptAssembler 组装 system prompt
    → CrawlLoop.run()
      → step: LLM tool calling
      → execute_tool_batch: 工具执行（经 hook 链）
      → followUp: LLM 判断是否继续
      → 循环直到 finish
    → RunResult 返回
```

### 3.2 Session 持久化
```
对话树 append-only
  → MySQL entries 表（每条 entry 不可变，只追加）
  → Lanes 记录 leaf 指针（指向各分支最新 entry）
  → Global Facts 最新写胜（覆盖更新）
```

### 3.3 崩溃恢复
```
operation_logs（每步操作前预写日志，预分配 ID）
  → 崩溃后重启
  → get_open_operations() 查询未完成操作
  → 从 checkpoint 恢复上下文
  → 继续执行未完成的工具调用
```

---

## 4. 关键设计模式

> 以下模式继承自 pi Agent Harness 设计哲学

### 4.1 Turn Snapshot 不可变
每次 LLM step 产生的 turn 记录一旦写入即不可修改。后续操作只能追加新条目，保证对话历史的完整性与可审计性。

### 4.2 Save Point 刷新
每轮循环结束后刷新保存点，确保中间状态持久化。崩溃恢复从最近的 save point 开始，而非从头重跑。

### 4.3 Hooks 先于效果
所有 hook 回调在副作用发生前执行，可以拦截、修改或中止即将发生的操作。保证扩展点始终有机会介入。

### 4.4 Append-only 树
对话树采用 append-only 策略，任何"修改"实际都是追加新条目。天然支持回溯、审计与分支。

### 4.5 Lanes 并行
同一会话内通过 lanes 支持多并行分支，各 lane 独立推进，互不阻塞。适用于同时爬取多个站点 / 页面的场景。

### 4.6 Durability（操作日志 + 预分配 ID）
每个操作在执行前先写入 operation_logs 并预分配 ID，确保操作至少执行一次（at-least-once 语义）。崩溃恢复时通过 `get_open_operations` 定位未完成操作。

### 4.7 结果而非异常（RunResult）
所有执行结果通过 `RunResult` 判别联合返回，不使用异常表示正常业务状态。4 种 kind 覆盖全部可能：`completed` / `needs_input` / `error` / `cancelled`。

### 4.8 Compaction 内建
上下文压缩作为一等公民内建于 Harness，token 超限时自动触发。避免无限增长的上下文导致 LLM 调用失败。

---

## 5. 扩展点

| 扩展点 | 方式 | 示例 |
|--------|------|------|
| 新工具 | 注册 `CrawlToolDef` + executor | 自定义导出工具 / 数据清洗工具 |
| 新 Hook | 注册 `HookCallback` 到对应事件 | 日志增强 / 限流策略 / 内容过滤 |
| 新 Lane | `session.create_lane(name)` | 并行爬取多个站点 |
| 新引擎 | 实现 `BaseEngine` + 加入 fallback chain | 自定义 HTTP 客户端 / 新浏览器引擎 |
| 新 LLM Provider | 实现 `langchain BaseChatModel` + 注册 factory | Anthropic / Moonshot / 自建模型 |
| 新提取策略 | 继承 `BaseExtractor` | 正则提取 / JSONPath / GraphQL 解析 |

---

## 6. 运维与监控

### 6.1 日志
- **loguru** + **structlog** 结构化日志
- 敏感数据（Cookie / Token / 完整响应体）不记录

### 6.2 关键指标
- `pages_crawled` / `items_extracted` / `errors` / `duration`
- 域名级成功率、平均延迟、重试率
- Session 活跃数、Lane 并行度
- LLM 调用次数 / token 消耗

### 6.3 故障恢复
- **operation_logs**：预分配 ID + 未完成操作检测 → 自动续跑
- **Save Point**：每轮循环结束刷新，崩溃后从最近保存点恢复
- **MySQL 持久化**：对话树与操作日志均落盘，进程重启不丢失

---

## 7. 安全与合规

| 措施 | 说明 |
|------|------|
| 域名限速 | 令牌桶算法，默认 0.5 req/s，可配置 |
| 代理轮换 | HTTP/SOCKS5 代理池，支持轮换策略 |
| UA 轮换 | 内置多种主流 UA + 自定义池 |
| 敏感数据不记录 | 日志不记录 Cookie / Token / 完整响应体 |
| robots.txt | 可选检查，默认尊重 |
| 仅个人使用 | 不提供分发 / 商业服务接口 |

---

## 8. 部署

```yaml
# docker-compose 核心服务
services:
  mysql:
    image: mysql:8.0
    # entries / operation_logs / global_facts 持久化

  redis:
    image: redis:7
    # 缓存 / 限速 / 会话状态

  crawagent:
    build: .
    # FastAPI + Uvicorn
    # /api/harness/* + 旧端点

  nginx:
    image: nginx:latest
    # 反向代理 + WebSocket 支持
```

---

## 9. 版本历史

| 版本 | 日期 | 变更 |
|------|------|------|
| v1.0 | 2026-07-12 | 初始版本：LangGraph 状态机架构，5 阶段实现 |
| v2.0 | 2026-07-31 | pi Agent Harness 重构：CrawlHarness/CrawlLoop/CrawlSession/CrawlHooks/Compaction 等新模块，MySQL 持久化，8 种 hook 事件，7 个内置工具 |
| v2.1 | 2026-08-01 | P1 引擎抽象层（engines/三引擎fallback）、P6 深度爬取+URL过滤器+饱和度感知、P7 视频提取+广告移除+yt-dlp播放列表、代理轮换、登录态持久化、反爬Hook（antibot/escalation） |

---

## 附录：核心文件清单

```
crawagent/
├── __init__.py
├── config/
│   └── settings.py             # Pydantic Settings（含 MySQL/Redis 配置）
├── core/
│   ├── database.py             # SQLAlchemy 2.0 async + MySQL
│   ├── fetcher.py              # httpx + curl_cffi（旧引擎，兼容保留）
│   ├── frontier.py             # URL 队列
│   ├── extractor.py            # 三级回退（8 种提取器）
│   ├── retriever.py            # 全文检索
│   ├── models.py               # Pydantic 模型
│   ├── executor.py             # 执行器
│   ├── proxy.py                # 代理配置 + 轮换代理池（P1-7）
│   ├── deep_crawl.py           # BFS/DFS/Best-First 深度爬取（P6-3）
│   ├── adaptive.py             # 饱和度感知爬取（P6-4）
│   └── filters.py              # URLFilter + FilterChain（P6-5）
├── engines/                    # 引擎抽象层（P1-4/P1-5）
│   ├── __init__.py             # 统一导出
│   ├── base.py                 # BaseEngine 抽象基类 + EngineConfig
│   ├── httpx_engine.py         # httpx 引擎（最快，无 JS）
│   ├── curl_cffi_engine.py     # curl_cffi 引擎（TLS 指纹绕过）
│   ├── playwright_engine.py    # Playwright 引擎（JS 渲染 + JS 注入 + 登录态）
│   └── fallback.py             # FallbackChain waterfall 执行器
├── extractors/                 # 提取器模块（P7-2/P7-3）
│   ├── __init__.py
│   ├── video_extractor.py     # <video>/<source>/iframe/m3u8/mp4 URL 提取
│   └── ad_remover.py           # DOM 广告移除（CSS选择器+脚本域名+空容器）
├── harness/                    # pi Agent Harness Python 实现
│   ├── __init__.py             # 统一导出
│   ├── types.py                # 核心类型定义（CrawlToolDef / RunResult / RecoveryPlan）
│   ├── session.py              # MySQL 持久化会话（含 IdPool + 崩溃恢复）
│   ├── hooks.py                # 8 种 hook 事件
│   ├── loop.py                 # driverLoop
│   ├── harness.py              # CrawlHarness 主类
│   ├── tools.py                # 工具集定义 + 执行器注册
│   ├── env.py                  # 执行环境抽象
│   ├── compaction.py           # 上下文压缩（含 rebuild_context）
│   ├── system_prompt.py        # 四段式提示词组装
│   ├── antibot_hook.py        # before_tool 反爬拦截（P1-1）
│   └── escalation_hook.py     # after_response 自动引擎升级（P1-2）
├── sessions/                   # 登录态管理（P1-8/P7-1）
│   ├── __init__.py
│   └── profile_manager.py     # Playwright persistent_context 登录态持久化
├── js_snippets/                # JS 注入片段（P1-6）
│   ├── __init__.py             # 片段加载器
│   ├── navigator_overrider.js  # navigator 属性覆盖（11 项反检测）
│   └── remove_overlay.js       # 弹窗/遮罩/广告移除
├── security/                   # 安全扫描（P5）
│   ├── models.py               # Vulnerability / ScanTask / SecurityStore
│   ├── vuln_scanner.py        # OWASP Top 10 扫描引擎
│   ├── auto_fixer.py          # 漏洞修复补丁生成
│   ├── network_capture.py     # 浏览器网络请求捕获
│   └── security_hook.py       # 安全 Hook 处理器
├── monitor/                    # 监控模块（P4）
│   ├── scheduler.py            # APScheduler + Redis
│   ├── diff_detector.py       # 变化检测 + 结构化 diff
│   ├── notifier.py            # Webhook / 飞书 / 邮件
│   └── baseline.py             # MySQL 基线快照
├── output/                     # 文件整理 + 媒体下载（P3/P7）
│   ├── __init__.py
│   ├── organizer.py           # FileOrganizer（路径模板引擎）
│   └── media_downloader.py   # yt-dlp + httpx 流式（含 extract_info/download_playlist）
├── graph/                      # 旧 Agent 编排（兼容）
│   ├── agent_workflow.py
│   ├── site_analyzer.py
│   ├── anti_bot.py             # 反爬检测（含 is_blocked() 三层检测）
│   └── prompts/
├── llm/
│   ├── factory.py              # 多 Provider 工厂
│   └── prompts.py
├── api/
│   └── server.py               # FastAPI（含 /api/harness/* /api/video/* 等端点）
└── cli/
    └── main.py                 # Click CLI
```

---

*文档维护：随代码演进同步更新*
