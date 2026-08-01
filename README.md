# CrawAgent

基于 LLM Agent 的智能爬虫系统 - 生产级、可扩展、模型无关。

## 🎯 项目定位

**核心理念**：把 LLM 当"大脑"（通过 OpenAI 兼容接口接入任意模型），把爬虫引擎、反爬、存储、调度当"身体+神经系统"自己造。模型是商品，工程才是护城河。

- **是什么**：生产级智能爬虫 Agent 框架
- **目标**：给任意 URL → 自动分类 → 交互问"爬什么" → 自动发现站点数据接口 → 规模化抓取 → 结构化落库
- **技术栈**：Python 3.11+ / LangGraph / FastAPI / httpx + curl_cffi / Playwright / SQLite FTS5 / Pydantic v2
- **用途**：个人学习/研究，不分发不售卖

## 🏗️ 架构总览

```
用户指令
   │
   ▼
┌─────────────────────────────────────┐
│ Main Agent（主控脑，唯一对话入口）     │
│  - 分类页面类型                       │
│  - 规划 CrawlPlan                     │
│  - 派活给子 Agent / 工具               │
│  - 关键决断（反爬升级/翻页/终止）       │
└──────┬──────────────┬────────────────┘
       │              │
       │              └──▶ AntiBot Agent（子，可选）
       │                    反爬升级决策
       ▼
┌─────────────────────────────────────┐
│ SiteAnalyzer 子图（重推理子 Agent）    │
│  - Playwright 加载 + 拦截网络          │
│  - 截 DOM 片段 + 网络摘要               │
│  - LLM 推理产出：selectors / api / 分页 │
│  - 内部自带循环：发现端点 → 理解 → 再发现 │
└──────────┬────────────────────────────┘
           │ 产出：SiteAnalysis（结构化规则）
           ▼
┌─────────────────────────────────────┐
│ CrawlExecutor（纯代码，非 Agent！）    │
│  Frontier → Fetcher → Extractor → Store │
│  - 高频确定性活，不调 LLM               │
│  - 被 Main 当工具调用                   │
└─────────────────────────────────────┘
```

**核心原则**：
- **混合式 + 最多 2 层**：主 Agent + 1-2 个专项子 Agent（SiteAnalyzer 必选、AntiBot 可选）
- **执行层全是代码**：Frontier/Fetcher/Extractor 不调 LLM，快、省、稳
- **大脑外包**：任意 OpenAI 兼容 API（DeepSeek/OpenAI/智谱/百川/月之暗面/...）或本地 Ollama，改配置即换模型

## 📦 核心组件

| 组件 | 文件 | 职责 |
|------|------|------|
| **Fetcher** | `core/fetcher.py` | httpx + curl_cffi，指数退避+抖动重试、域名令牌桶限速、代理轮换、UA轮换、curl_cffi impersonate |
| **Frontier** | `core/frontier.py` | SQLite + FTS5 队列、去重（URL规范化+内容哈希）、断点续爬、优先级+深度调度 |
| **Extractor** | `core/extractor.py` | 三级回退：CSS → XPath → LLM，输出 Pydantic 模型，动态 Schema |
| **Retriever** | `core/retriever.py` | SQLite FTS5 全文检索（BM25），预留向量检索接口 |
| **SiteAnalyzer** | `graph/site_analyzer.py` | Playwright 加载+抓包 → LLM 逆向接口+选择器+分页 |
| **AntiBot** | `graph/anti_bot.py` | LLM 决定升级策略：wait → proxy → curl_cffi → browser → captcha → human |
| **Agent Workflow** | `graph/agent_workflow.py` | LangGraph 状态机：classify → plan → analyze → execute → decide |

## 🚀 快速开始

### 1. 安装依赖

```bash
# 核心依赖
pip install -e . --no-build-isolation

# 可选：浏览器自动化
pip install playwright && playwright install chromium

# 可选：反爬指纹
pip install browserforge
```

### 2. 配置模型

```bash
# .env（只填你要用的）
# OpenAI 兼容接口（DeepSeek/OpenAI/智谱/百川/月之暗面/...）
OPENAI_API_KEY=your_key
OPENAI_BASE_URL=https://api.deepseek.com/v1  # 换成你的 API 地址
DEFAULT_MODEL=deepseek-chat                   # 换成你要的模型

# 或用 Ollama 本地模型
# OLLAMA_BASE_URL=http://localhost:11434
# DEFAULT_MODEL=qwen2.5:7b
```

### 3. 运行

```bash
# 初始化数据目录
python main.py init

# 环境检查
python main.py check

# 交互式运行 Agent
python main.py run "爬取 https://example.com 的文章标题和链接" --max-pages 20

# 直接爬取（不经过 Agent 规划）
python main.py crawl "https://example.com" --selector title=.title a --max-pages 10

# 分析站点结构
python main.py analyze "https://example.com"

# 启动 API 服务
python main.py serve --port 8000
```

### 4. API 调用

```bash
# 启动 Agent 任务
curl -X POST http://localhost:8000/agent/run \
  -H "Content-Type: application/json" \
  -d '{"instruction": "爬取 https://news.ycombinator.com 的标题和链接", "max_pages": 5}'

# 分析站点
curl -X POST http://localhost:8000/agent/analyze \
  -H "Content-Type: application/json" \
  -d '{"url": "https://example.com"}'

# 直接爬取
curl -X POST http://localhost:8000/crawl/direct \
  -H "Content-Type: application/json" \
  -d '{"urls": ["https://example.com"], "max_pages": 10}'
```

## 📂 项目结构

```
CrawAgent/
├── main.py                    # CLI 入口
├── pyproject.toml             # 依赖与构建配置
├── .env.example               # 环境变量模板
├── data/                      # SQLite 数据目录
├── logs/                      # 日志目录
├── crawagent/
│   ├── __init__.py            # 统一导出
│   ├── config/
│   │   └── settings.py        # Pydantic Settings 配置
│   ├── core/                  # 身体：纯工程组件
│   │   ├── fetcher.py         # 抓取器
│   │   ├── frontier.py        # 前沿/队列/去重
│   │   ├── extractor.py       # 提取器（三级回退）
│   │   ├── retriever.py       # 检索器
│   │   ├── executor.py        # 执行器
│   │   └── models.py          # Pydantic 模型
│   ├── graph/                 # 神经系统：Agent 编排
│   │   ├── agent_workflow.py  # 主图
│   │   ├── site_analyzer.py   # SiteAnalyzer 子图
│   │   ├── anti_bot.py        # AntiBot 子图
│   │   └── prompts/           # 提示词
│   ├── llm/                   # 大脑接口
│   │   ├── factory.py         # 多 Provider 工厂
│   │   └── prompts.py         # 通用提示词
│   ├── api/
│   │   └── server.py          # FastAPI 服务
│   └── cli/
│       └── main.py            # Click CLI
└── tests/                     # 测试（待补充）
```

## ⚙️ 关键技术决策

| 决策 | 选择 | 理由 |
|------|------|------|
| **异步框架** | `asyncio` + `httpx` | I/O 密集、单线程万并发、原生 HTTP/2 |
| **反爬指纹** | `curl_cffi` impersonate | TLS 指纹伪装 Chrome/Firefox/Safari，零配置 |
| **浏览器自动化** | `Playwright` | 稳、支持网络拦截、CDP、无头模式成熟 |
| **队列/去重/检索** | `SQLite + FTS5` | 零运维、单文件、事务、BM25 全文、断点续爬 |
| **结构化输出** | `Pydantic v2` | 运行时验证、Schema 即契约、LLM function calling 友好 |
| **Agent 编排** | `LangGraph` | 图状状态机、子图、Checkpointer、人工介入、流式 |
| **多模型抽象** | `langchain_core.BaseChatModel` | 统一接口，换模型改配置，支持 tool calling |
| **结构化提取** | CSS → XPath → LLM 三级回退 | 选择器优先（快/准），LLM 兜底（通用） |

## 📈 演进路线

| 阶段 | 目标 | 关键任务 |
|------|------|----------|
| **Phase 1** ✅ | 地基 | Frontier + Fetcher（限速/重试/去重/续爬） |
| **Phase 2** ✅ | 结构化 | Extractor 三级回退 + Pydantic 动态 Schema |
| **Phase 3** ✅ | 编排 | LangGraph 状态机：classify → plan → analyze → execute → decide |
| **Phase 4** ✅ | 反爬实战 | curl_cffi impersonate + browserforge stealth + 升级策略 |
| **Phase 5** ✅ | 差异化 | SiteAnalyzer（抓包+LLM逆向接口+选择器+分页） |
| **Phase 6+** | 难站叠能力 | 登录/Cookie注入 → 签名接口逆向 → 验证码 → 视频流解密（逐平台专项） |

## 📂 配置说明

```bash
# .env 示例
# 任意 OpenAI 兼容 API（DeepSeek/OpenAI/智谱/百川/月之暗面/...）
OPENAI_API_KEY=sk-xxx
OPENAI_BASE_URL=https://api.deepseek.com/v1
DEFAULT_MODEL=deepseek-chat

# 或本地 Ollama
# OLLAMA_BASE_URL=http://localhost:11434
# DEFAULT_MODEL=qwen2.5:7b

# 爬虫行为
PER_DOMAIN_RATE=0.5      # req/sec per domain
MAX_CONCURRENT=50
REQUEST_TIMEOUT=30.0
MAX_RETRIES=3
IMPERSONATE=chrome120
USE_CURL_CFFI=true
```

## 🔒 安全与合规

| 措施 | 实现 |
|------|------|
| 礼貌爬取 | 域名限速（默认 0.5 req/s）+ `robots.txt` 可选检查 |
| 用户代理轮换 | 内置 5 种主流 UA + 自定义池 |
| 代理支持 | HTTP/SOCKS5 代理池轮换 |
| 敏感数据不记录 | 日志不记录 Cookie/Token/完整响应体 |
| 仅个人使用 | 不提供分发/商业服务接口 |

## 📝 文档

- [架构设计文档](ARCHITECTURE.md) - 详细架构设计、数据流、接口定义
- [API 文档](http://localhost:8000/docs) - 启动服务后访问 Swagger UI

## 📄 许可

个人学习项目，仅供研究学习使用。