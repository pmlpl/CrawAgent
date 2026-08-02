# CrawAgent

基于 pi Agent Harness 的智能爬虫系统 — 真正的 Agent，不是固定流水线。

## 项目定位

**核心理念**：以 pi Agent Harness 为地基，LLM 在 loop 中自主选工具，hooks 拦截/修改行为，lanes 并行处理多任务。

- **是什么**：生产级智能爬虫 Agent 框架（Agent Harness 架构）
- **目标**：给任意 URL → LLM 自主决策 → 工具调用 → 结构化落库
- **技术栈**：Python 3.11+ / FastAPI / Vue 3 / MySQL 8.0 / Redis 7 / Playwright
- **用途**：个人学习/研究，不分发不售卖

## 核心能力

| 能力 | 说明 | 状态 |
|------|------|------|
| **Agent Harness** | LLM 自主决策 loop + 8 种 hooks + lanes 并行 | ✅ |
| **三引擎 fallback** | httpx → curl_cffi → Playwright，自动升级 | ✅ |
| **反爬检测** | 三层检测（状态码+响应头+响应体），7 种挑战类型 | ✅ |
| **深度爬取** | BFS/DFS/Best-First + URL 过滤器链 + 饱和度感知 | ✅ |
| **文件整理** | 路径模板引擎，按 `domain/date/title.md` 自动落盘 | ✅ |
| **监控场景** | 定时爬取 + diff 检测 + Webhook/飞书告警 | ✅ |
| **安全扫描** | OWASP Top 10 漏洞扫描 + 自动修复补丁 | ✅ |
| **视频下载** | yt-dlp 集成 + 播放列表下载 + 本地播放器 | ✅ |
| **上下文压缩** | 长任务 token 超限自动压缩 + 崩溃恢复 | ✅ |
| **登录态持久化** | Playwright persistent_context，跨会话复用 cookie | ✅ |
| **代理轮换** | HTTP/SOCKS5 代理池 + 健康检查 + 冷却恢复 | ✅ |
| **图片专用提取** | img 懒加载/meta og/JSON-LD/背景图多源提取 + 去重分类 | ✅ |
| **SPA 客户端路由** | Nuxt/Vue/React/Next 4xx 自动等待路由渲染并改写 200 | ✅ |
| **懒加载触发** | Playwright 滚动到底部，触发 data-src/data-original 加载 | ✅ |
| **网站画像系统** | 自动发现技术栈/图片加载/反爬等级/导航结构，支持针对性优化 | ✅ |
| **工具调用完整性** | tool_calls/tool_response 配对自愈，避免 OpenAI 400 错误 | ✅ |
| **API 签名捕获** | 浏览器拦截 XHR/fetch 签名 API（无需逆向算法），滚动分页 + 媒体直链提取 | ✅ |

## 架构总览

```
用户指令
   │
   ▼
┌─────────────────────────────────────────┐
│        CrawlHarness (编排层)             │
│  session + hooks + lanes + tools        │
├─────────────────────────────────────────┤
│        CrawlLoop (driverLoop)            │
│  checkpoint → step(LLM) → tools → loop  │
├─────────────────────────────────────────┤
│  Tools: crawl/extract/analyze/save/     │
│         search/monitor/scan_vuln        │
├─────────────────────────────────────────┤
│  Engines: httpx → curl_cffi → playwright │
│  (FallbackChain waterfall)              │
├─────────────────────────────────────────┤
│  MySQL (session持久化) + Redis (缓存/限速)│
└─────────────────────────────────────────┘
```

详细设计见 [ARCHITECTURE.md](ARCHITECTURE.md)。

## 快速开始

### 1. 安装依赖

```bash
# 核心依赖
pip install -e .

# 可选：浏览器引擎（反爬/JS渲染需要）
pip install playwright && playwright install chromium

# 可选：TLS 指纹绕过
pip install curl_cffi

# 可选：视频下载
pip install yt-dlp
```

### 2. 配置环境

```bash
# .env
OPENAI_API_KEY=your_key
OPENAI_BASE_URL=https://api.deepseek.com/v1
DEFAULT_MODEL=deepseek-chat

# MySQL + Redis（用 docker-compose 启动）
docker-compose up -d mysql redis
```

> 完整容器化部署（后端 + 前端 + MySQL + Redis 一键拉起）见 [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)。

### 3. 运行

```bash
# 启动后端 API
python -m crawagent.api.server

# 启动前端
cd frontend && npm install && npm run dev
```

### 4. 前端页面

| 页面 | 路径 | 功能 |
|------|------|------|
| 对话 | `/chat` | Agent 交互主界面 |
| 任务 | `/tasks` | 任务列表管理 |
| 输出 | `/output` | 文件整理 + 预览 |
| 监控 | `/monitor` | 监控任务 + 告警 |
| 安全 | `/security` | 漏洞扫描 + 修复 |
| 视频 | `/video` | 视频下载 + 本地播放 + API 捕获（强风控/JS 签名站） |
| 设置 | `/settings` | 系统配置 |

## 项目结构

```
CrawAgent/
├── crawagent/
│   ├── harness/                # Agent Harness 核心
│   │   ├── harness.py          # CrawlHarness 主类
│   │   ├── loop.py             # driverLoop
│   │   ├── session.py          # MySQL 持久化 + 崩溃恢复
│   │   ├── hooks.py            # 8 种 hook 事件
│   │   ├── tools.py            # 7 个内置工具
│   │   ├── compaction.py       # 上下文压缩
│   │   ├── antibot_hook.py     # 反爬拦截 hook
│   │   └── escalation_hook.py  # 引擎升级 hook
│   ├── engines/                # 引擎抽象层
│   │   ├── base.py             # BaseEngine
│   │   ├── httpx_engine.py     # httpx（最快）
│   │   ├── curl_cffi_engine.py # curl_cffi（TLS 指纹）
│   │   ├── playwright_engine.py # Playwright（JS 渲染）
│   │   └── fallback.py         # FallbackChain waterfall
│   ├── extractors/             # 提取器
│   │   ├── video_extractor.py  # 视频 URL 提取
│   │   └── ad_remover.py       # DOM 广告移除
│   ├── core/                    # 核心组件
│   │   ├── fetcher.py / frontier.py / extractor.py
│   │   ├── site_profile.py     # 网站画像系统（针对性优化）
│   │   ├── proxy.py            # 代理轮换池
│   │   ├── deep_crawl.py       # BFS/DFS/Best-First
│   │   ├── adaptive.py         # 饱和度感知
│   │   └── filters.py          # URL 过滤器链
│   ├── security/               # 安全扫描
│   ├── monitor/                # 监控模块
│   ├── output/                 # 文件整理 + 媒体下载
│   ├── sessions/               # 登录态持久化
│   ├── js_snippets/            # JS 注入片段
│   ├── api/server.py           # FastAPI 服务
│   └── cli/main.py             # CLI
├── frontend/                   # Vue 3 前端
├── docker/                     # 部署资产（Dockerfile / nginx.conf / mysql init）
├── docker-compose.yml          # 一键编排（mysql + redis + backend + frontend）
├── docs/DEPLOYMENT.md          # 部署与运维指南
└── .env.example                # 环境变量模板
```

## 开发路线图

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

详见 [DEVELOPMENT_PLAN.md](DEVELOPMENT_PLAN.md)。

## 关键技术决策

| 决策 | 选择 | 理由 |
|------|------|------|
| **Agent 架构** | pi Agent Harness（loop+hooks+lanes） | 比 LangGraph 状态机更适合自主决策 |
| **引擎 fallback** | httpx → curl_cffi → Playwright | 由快到慢，按需升级 |
| **持久化** | MySQL + 操作日志 + 预分配 ID | 崩溃恢复，at-least-once 语义 |
| **上下文压缩** | LLM 摘要 + 保留最近 N 轮 | 长任务不超限 |
| **视频下载** | yt-dlp | 支持 YouTube/Bilibili 等 |
| **前端** | Vue 3 + Vite | 轻量、快速 |

## 文档

- [架构设计](ARCHITECTURE.md) — 详细架构设计、数据流、接口定义
- [开发计划](DEVELOPMENT_PLAN.md) — 开发路线图、验收标准
- [已知缺口](KNOWN_GAPS.md) — 待修复问题清单
- [API 文档](http://localhost:8000/docs) — 启动服务后访问 Swagger UI

## 许可

个人学习项目，仅供研究学习使用。
