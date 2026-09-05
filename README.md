# CrawAgent

LLM 驱动的智能爬虫 Agent 框架。基于 LangChain + LangGraph，内置 17+ 爬虫工具、浏览器渲染、MCP 协议扩展，支持会话持久化、技能插件化。

## 快速开始

### 环境要求

- Python 3.11+
- [uv](https://docs.astral.sh/uv/)（包管理）
- 可选：Playwright 浏览器内核（`uv run playwright install chromium`）

### 安装

```bash
# 克隆后安装（editable 模式，改代码立即生效）
uv sync --group dev
uv sync --group browser   # 需要浏览器渲染时
uv sync --group api       # 需要 FastAPI Web 后端时

# 可选依赖
uv run playwright install chromium   # SPA/动态页面爬取
```

### 配置

复制 `.env.example` 为 `.env`，修改必要字段：

```bash
cp .env.example .env
```

核心配置项：

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `OPENAI_API_KEY` | DeepSeek API Key | — |
| `OPENAI_BASE_URL` | API 地址 | `https://api.deepseek.com/v1` |
| `DEFAULT_MODEL` | 默认模型 | `deepseek-v4-flash` |
| `THINKING_DEPTH` | 思考深度 `off/low/medium/high/max` | `off` |
| `DOWNLOADS_DIR` | 媒体下载目录 | `./downloads` |
| `OUTPUT_DIR` | 文本产物目录 | `./output` |

### 启动后端

```bash
# 开发模式（热重载）
uv run python -m uvicorn crawagent.web.server:app --host 0.0.0.0 --port 8006 --reload

# 生产模式
uv run python -m uvicorn crawagent.web.server:app --host 0.0.0.0 --port 8006
```

启动成功后：

```
INFO:     Started server process [12345]
INFO:     Uvicorn running on http://0.0.0.0:8006
```

## 工具列表

Agent 默认装配以下工具（共 20 个）：

### 爬虫核心

| 工具 | 说明 |
|------|------|
| `crawl_webpage` | requests 直连爬取 HTML 原始内容（不渲染 JS） |
| `browse_and_crawl` | Playwright 无头浏览器，处理 SPA/JS 动态页面 |
| `extract_content` | HTML → Markdown 正文提取 |
| `extract_list` | 列表页/索引页条目提取（标题+URL 配对） |
| `web_search` | 联网搜索第三方站点 |

### 数据持久化

| 工具 | 说明 |
|------|------|
| `save_record` | 保存抽取内容到 SQLite 数据库 |
| `list_crawled_resources` | 查询已爬取资源 |
| `save_to_file` | 保存文本到本地文件（md/txt/json） |
| `download_images` | 批量下载图片/视频到 downloads 目录 |

### 站点专用

| 工具 | 说明 |
|------|------|
| `extract_social_media` | 抖音/小红书/B站 视频/图文提取 |
| `download_social_media` | 社交媒体媒体文件下载 |
| `extract_wallpaper_list` | 壁纸/图片站列表抽取 |
| `wallpaper_detail` | 单条壁纸详情抓取 |
| `list_weread_chapters` | 微信读书章节列表 |
| `get_weread_chapter` | 微信读书章节正文 |
| `list_site_profiles` | 已保存站点档案列表 |
| `save_site_profile` | 保存站点 cookies/脚本/策略 |

### 辅助

| 工具 | 说明 |
|------|------|
| `run_custom_script` | 写并运行自定义 Python 脚本（沙盒内） |
| `video_site_expert` | 视频站点子 Agent（自动选址+探测） |
| `read_skill` | 按需读取 skill 插件正文 |

### MCP 扩展（可选）

| 工具 | 说明 |
|------|------|
| `wait_capture_ready` | 等待抓包会话就绪 |
| `check_mcp_status` | 检查 MCP Server 状态 |

## 架构

```
crawagent/
├── config/settings.py          # pydantic-settings，.env 加载
├── graph/
│   ├── agent.py                 # LangGraph StateGraph + SYSTEM_PROMPT 构建
│   ├── skills.py                # Skill 插件加载 + MCP 自动启动
│   ├── middleware.py            # 历史消息滑动窗口裁剪
│   ├── script_forcer.py         # 工具失败 → 自动切换脚本兜底
│   └── subagents/
│       └── video_finder.py      # 视频站点子 Agent
├── llm/model.py                 # get_llm() — DeepSeek 为主，LangChain 抽象
├── observability/metrics.py     # Token 统计、缓存命中追踪
├── prompts/                     # system.md + infinite-gen-2.md（导入时拼接）
├── tools/                       # 19 个工具模块
└── web/
    ├── server.py                # FastAPI，端口 8006，启动时 daemon 清理
    ├── routers/                 # sessions / settings / sites
    └── state.py                 # get_agent / get_checkpointer 单例
```

数据流：

```
User Input → FastAPI (/api/sessions)
           → get_agent() (LangGraph compiled)
           → SYSTEM_PROMPT + LLM (DeepSeek) + Tools
           → checkpointer (SqliteSaver) 持久化会话
           → Response → 前端
```

## 开发

### 验证导入

```bash
uv run python -c "from crawagent.graph.agent import SYSTEM_PROMPT; from crawagent.web.server import app; print('OK')"
```

### 运行测试

```bash
uv run pytest tests/ -v
```

### 类型检查

```bash
uv run pyright crawagent
```

## 目录结构（运行时）

```
project_root/
├── data/
│   ├── crawagent.db             # SQLite 爬取数据
│   ├── sessions.db              # LangGraph 会话状态
│   ├── _tmp/                    # 临时文件（>1h 自动清理）
│   └── sites.json               # 站点档案
├── downloads/                   # 媒体下载（图片/视频/壁纸）
├── output/                      # 文本产物（md/txt/json）
└── logs/                        # 运行日志（>30d 自动清理）
```

## 许可证

MIT
