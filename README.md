<div align="center">

<img src="assets/logo-concepts/concept-A-spider.png" width="180" alt="CrawAgent">

# CrawAgent

**吐丝 · 结网** — LLM 驱动的智能爬虫 Agent 框架

</div>

基于 LangChain + LangGraph，内置 **43 个爬虫工具**、6 个攻略技能、浏览器渲染、MCP 协议扩展（fetch / playwright / 抓包分析）、第三方插件规范（plugins/）、分布式任务队列与 Android 逆向能力，支持会话持久化。

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
uv sync --extra dist      # 需要分布式爬虫时（Redis）

# 可选：浏览器内核
uv run playwright install chromium   # SPA/动态页面爬取

# 可选：Android 逆向（不写入主依赖，避免冲突）
pip install frida frida-tools
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

### 启动

```bash
uv run crawagent start
# → http://127.0.0.1:8006
```

启动时自动检测 / 下载 / 拉起嵌入 Redis（分布式模式启用时）。**不会自动弹浏览器**——在 WebUI 内手动打开，或直接访问 `http://127.0.0.1:8006`。

环境变量可调：`CRAWAGENT_PORT`（端口，默认 8006）、`CRAWAGENT_HOST=0.0.0.0`（局域网监听，不弹浏览器）、`CRAWAGENT_NO_OPEN=1`（禁止弹浏览器）。

开发模式（前端热更新）：

```bash
# 终端 1：后端
uv run crawagent start
# 终端 2：前端
cd web && npm run dev
# → http://127.0.0.1:5173（/api 与 /ws 自动代理到 8006）
```

## 能力总览

| 能力域 | 说明 |
|------|------|
| 自主工具循环 | 43 个内置工具，LLM 自主决策调用；多数对话零工具调用 |
| 置信度自动升级 | 提取结果 <60 分自动切 Playwright 浏览器重抓（零 LLM 成本） |
| 兜底武器 | `run_custom_script`：Agent 现场写 Python 脚本，专治内置工具搞不定的站 |
| 站点档案 | 首次成功后保存策略/脚本/Cookie，同站复用跳过分析 |
| 代理池 | 5 个代理管理工具：增删 / 探活 / 轮询 / 失败标记 |
| 模拟登录 | Playwright 自动表单登录 + Cookie 存档复用 + 登录状态校验 |
| 分布式爬虫 | Redis checkpointer + 任务队列 + worker 心跳崩溃恢复（`uv sync --extra dist`） |
| Android 逆向 | frida hook：定位加密函数 / dump SO / 绕过 SSL pinning |
| MCP 扩展 | fetch / playwright / 自研抓包分析，聊天即加 MCP server |
| 插件规范 | `plugins/` 下第三方插件自动接入（工具 + 技能） |
| 会话持久化 | SqliteSaver / RedisSaver，重启后继续聊 |
| 流式 WebUI | Vue 3 + WebSocket，token 级输出 + 工具轨迹 + 事件断线重放 |

## 工具列表

Agent 默认装配 **43 个内置工具**（+ 1 个示例插件工具 `fetch_rss_feed`）。完整清单与用法见 [wiki/03-工具与技能总览.md](wiki/03-工具与技能总览.md)。

### 基础爬取与提取

`crawl_webpage` · `browse_and_crawl` · `extract_content` · `extract_list` · `extract_list_paged`

### 存储与查询

`save_record` · `list_crawled_resources` · `search_knowledge` · `save_to_file` · `download_images`

### 站点专用

`extract_social_media` · `download_social_media` · `extract_wallpaper_list` · `wallpaper_detail` · `list_weread_chapters` · `get_weread_chapter` · `list_site_profiles` · `save_site_profile`

### 代理池（5）

`add_proxy` · `remove_proxy` · `mark_proxy_failed` · `get_proxy` · `list_proxies`

### 模拟登录（2）

`login_site` · `check_login_status`

### MCP 管理（4）

`list_mcp_servers` · `add_mcp_server` · `remove_mcp_server` · `disable_mcp_server`

### 高级工具（3）

`markitdown_convert` · `crawl4ai_deep_crawl` · `browser_use_navigate`

### Android 逆向（6）

`list_adb_devices` · `install_apk` · `push_file` · `frida_hook_function` · `frida_dump_so` · `frida_bypass_ssl_pinning`

### 辅助

`run_custom_script` · `video_site_expert` · `ask_user` · `recommend_scripts` · `read_skill`

### MCP 扩展（可选，.env 的 MCP_SERVERS 配置）

| 工具 | 说明 |
|------|------|
| `wait_capture_ready` | 等待抓包会话就绪 |
| `check_mcp_status` | 检查 MCP Server 状态 |
| `fetch`（mcp-server-fetch） | 第三方 MCP：网页 → LLM 友好 Markdown（官方 fetch server，stdio） |
| `browser_*` ×24（playwright-mcp） | 第三方 MCP：微软官方浏览器自动化 |
| navigate / filter_requests 等（anything-analyzer） | 自研 MCP：加密接口抓包分析 |

单个 MCP server 连不上只跳过它自己，不影响其它 server。

### 技能（SKILL.md）

`skills/` 目录内置 6 个爬虫攻略技能（Agent 构建时索引进 system prompt，正文由
`read_skill` 按需加载）：`bilibili-grab` / `douyin-grab` / `wallpaper-sites` /
`weread-grab` / `batch-crawl-playbook` / `custom-script-recipes`。
详见 [skills/README.md](skills/README.md)。

### 插件（plugins/）

第三方插件放进项目根 `plugins/` 下即自动接入（工具 + 技能一起生效）：

```bash
crawagent add-tool my_plugin                     # 从脚手架创建新插件包
crawagent add-plugin D:/path/to/plugin           # 从本地目录接入
crawagent add-plugin https://github.com/x/y.git  # 从 git 仓库接入
crawagent plugins                                # 查看已装插件
```

## 架构

```
crawagent/
├── config/settings.py          # pydantic-settings，.env 加载
├── dist/                        # 分布式：redis_client/redis_server/queue/pubsub/worker/runner/cli
├── graph/
│   ├── agent.py                 # LangGraph StateGraph + SYSTEM_PROMPT 构建
│   ├── skills.py                # Skill 插件加载 + MCP 自动启动
│   ├── middleware.py            # 历史消息裁剪（半水位淘汰，不污染持久态）
│   ├── script_forcer.py         # 工具失败 → 自动切换脚本兜底
│   └── subagents/
│       └── video_finder.py      # 视频站点子 Agent
├── llm/model.py                 # get_llm() — 多服务商适配，LangChain 抽象
├── observability/metrics.py     # Token 统计、缓存命中追踪
├── prompts/                     # system.md + infinite-gen-2.md（导入时拼接）
├── storage/                     # checkpointer 工厂 + meta_store + checkpoint_view
├── tools/                       # 43 个 @tool 工具 + _template/ 插件脚手架
│   ├── crawl/browse/extract/list_extract/save/query/file
│   ├── social/wallpaper/weread/site_profile
│   ├── proxy/login/android_reverse/advanced
│   └── script_tool/confidence/font_decrypt/pagination
├── plugins/                     # 第三方插件（plugin.json 规范）
├── skills/                      # 内置爬虫技能包（6 个 SKILL.md）
└── web/
    ├── server.py                # FastAPI，端口 8006
    ├── routers/                 # sessions / settings / sites / dist
    ├── state.py                 # get_agent / get_checkpointer 单例
    └── turn_engine.py           # 轮次事件泵
```

数据流：

```
User Input → FastAPI (/api/sessions)
           → get_agent() (LangGraph compiled)
           → SYSTEM_PROMPT + LLM + 43 工具
           → checkpointer (SqliteSaver / RedisSaver) 持久化会话
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

## 文档

| 文档 | 说明 |
|------|------|
| [wiki/](wiki/) | 用户向使用说明（快速开始、核心概念、工具总览、扩展能力、FAQ） |
| [docs/adr/](docs/adr/) | 架构决策记录 |
| [docs/architecture/](docs/architecture/) | 架构图 SVG |
| [升级改动文档/](升级改动文档/) | 编号变更规格与实施记录 |
| [CONTEXT.md](CONTEXT.md) | 术语词汇表 |

## 目录结构（运行时）

```
project_root/
├── data/
│   ├── crawagent.db             # SQLite 爬取数据
│   ├── sessions.db              # LangGraph 会话状态
│   ├── meta.db                  # 会话标题 / 错误记录
│   ├── proxies.json             # 代理池
│   ├── _tmp/                    # 临时文件（>1h 自动清理）
│   └── sites.json               # 站点档案
├── downloads/                   # 媒体下载（图片/视频/壁纸）
├── output/                      # 文本产物（md/txt/json）
└── logs/                        # 运行日志 + 会话归档
```

## 许可证

MIT
