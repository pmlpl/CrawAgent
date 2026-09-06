# CrawAgent

LLM 驱动的智能爬虫 Agent 框架。基于 LangChain + LangGraph，内置 21 个爬虫工具、6 个攻略技能、浏览器渲染、MCP 协议扩展（fetch / playwright / 抓包分析）与第三方插件规范（plugins/），支持会话持久化。

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

Agent 默认装配以下工具（共 21 个）：

### 爬虫核心

| 工具 | 说明 |
|------|------|
| `crawl_webpage` | requests 直连爬取 HTML 原始内容（不渲染 JS） |
| `browse_and_crawl` | Playwright 无头浏览器，处理 SPA/JS 动态页面 |
| `extract_content` | HTML → Markdown 正文提取 |
| `extract_list` | 列表页/索引页条目提取（标题+URL 配对） |
| `extract_list_paged` | 静态多页列表一次抓全：自动翻页（下一页链接 + page/p/pageNum 参数）+ 跨页去重合并 |
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

### MCP 扩展（可选，.env 的 MCP_SERVERS 配置）

| 工具 | 说明 |
|------|------|
| `wait_capture_ready` | 等待抓包会话就绪 |
| `check_mcp_status` | 检查 MCP Server 状态 |
| `fetch`（mcp-server-fetch） | 第三方 MCP：网页 → LLM 友好 Markdown（官方 fetch server，stdio） |
| `browser_*` ×24（playwright-mcp） | 第三方 MCP：微软官方浏览器自动化（导航/点击/截图/表单等） |
| navigate / filter_requests 等（anything-analyzer） | 自研 MCP：加密接口抓包分析（streamable-http） |

单个 MCP server 连不上只跳过它自己，不影响其它 server。

### 技能（SKILL.md）

`skills/` 目录内置 6 个爬虫攻略技能（Agent 构建时索引进 system prompt，正文由
`read_skill` 按需加载）：`bilibili-grab` / `douyin-grab` / `wallpaper-sites` /
`weread-grab` / `batch-crawl-playbook`（批量抓取总配方）/ `custom-script-recipes`
（脚本阶梯配方）。详见 [skills/README.md](skills/README.md)。

### 插件（plugins/）

第三方插件放进项目根 `plugins/` 下即自动接入（工具 + 技能一起生效）：

```bash
crawagent add-tool my_plugin                     # 从脚手架创建新插件包
crawagent add-plugin D:/path/to/plugin           # 从本地目录接入
crawagent add-plugin https://github.com/x/y.git  # 从 git 仓库接入
crawagent plugins                                # 查看已装插件
```

插件约定：`plugins/<name>/plugin.json`（manifest）+ `tools/*.py`（工具模块）+
`skills/*/SKILL.md`（技能包）。仓库自带示例插件 `plugins/example-rss/`。

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
├── tools/                       # 19 个工具模块 + _template/ 插件脚手架
├── plugins/                     # 第三方插件（plugin.json 规范，见 add-plugin）
├── skills/                      # 内置爬虫技能包（6 个 SKILL.md）
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
