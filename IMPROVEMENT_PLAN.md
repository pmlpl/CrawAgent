# CrawAgent 软件工程改进计划

> 本文档基于 2026-06-26 代码库健康审查编写，供 AI 编程助手逐项执行。

---

## 改进总览

| 优先级 | 数量 | 预计耗时 |
|--------|:----:|----------|
| 🔴 P0 立即修复 | 4 | 30 min |
| 🟠 P1 高优先级 | 6 | 3-4 h |
| 🟡 P2 中优先级 | 5 | 2-3 h |

---

## 🔴 P0 — 立即修复（必须最先做）

### P0-1：轮换 API Key，改为从环境变量读取

**当前问题**：[models.json](models.json) 中硬编码了真实 API Key：
```json
"api_key": "tp-xxxx"
```

**修改步骤**：

1. 在 [crawagent/config/settings.py](crawagent/config/settings.py) 的 `Settings._load()` 方法中，加载 `api_key` 后检查是否是占位符或环境变量引用：
   - 如果 `api_key` 以 `$` 开头，从 `os.environ` 读取（如 `$DEEPSEEK_API_KEY` → `os.environ["DEEPSEEK_API_KEY"]`）
   - 如果 `api_key` 为空字符串，也尝试从 `os.environ[f"{model.name.upper()}_API_KEY"]` 自动读取

2. 更新 [.env.example](.env.example)，补充完整的可用环境变量列表和说明。

3. 将 [models.json](models.json) 中的真实 API Key 替换为空字符串 `""`。

4. **用户侧**：去 MIMO 平台轮换该 Key（旧 Key 已在此报告中暴露）。

### P0-2：创建 `.gitignore`

在项目根目录创建 `.gitignore`，内容如下：

```gitignore
# 虚拟环境
.venv/
venv/
env/

# Python
__pycache__/
*.py[cod]
*.egg-info/
dist/
build/

# 运行时输出
output/
crawl_progress.json

# 敏感配置
models.json
.env

# IDE
.idea/
.vscode/
*.swp
*.swo

# OS
.DS_Store
Thumbs.db
```

### P0-3：修复数据库默认配置

**当前问题**：[crawagent/tools/database.py](crawagent/tools/database.py) 第 531-538 行硬编码了 MySQL 凭据：
```python
DEFAULT_DB_TYPE = "mysql"
DEFAULT_MYSQL_CONFIG = {
    "password": "123456",
    ...
}
```

**修改**：

1. 将 `DEFAULT_DB_TYPE` 改为 `"sqlite"`
2. 将数据库配置改为从环境变量读取：
```python
DEFAULT_DB_TYPE = os.environ.get("CRAWAGENT_DB_TYPE", "sqlite")
DEFAULT_MYSQL_CONFIG = {
    "host": os.environ.get("CRAWAGENT_MYSQL_HOST", "localhost"),
    "port": int(os.environ.get("CRAWAGENT_MYSQL_PORT", "3306")),
    "user": os.environ.get("CRAWAGENT_MYSQL_USER", "root"),
    "password": os.environ.get("CRAWAGENT_MYSQL_PASSWORD", ""),
    "database": os.environ.get("CRAWAGENT_MYSQL_DATABASE", "crawagent"),
}
```

### P0-4：移除项目根目录的临时测试脚本

以下文件不属于正式代码，移到 `tests/` 目录或删除：
- [full_crawler.py](full_crawler.py)
- [test_sina.py](test_sina.py)
- [test_weibo2.py](test_weibo2.py)

---

## 🟠 P1 — 高优先级

### P1-1：拆分 `base_crawler.py`（~1700 行 → 5 个文件）

**当前文件**：[crawagent/tools/base_crawler.py](crawagent/tools/base_crawler.py) 包含太多职责。

**拆分为**：

```
crawagent/tools/
├── base_crawler.py        # BaseCrawler + CrawlResult + _build_headers + URL检测 (~250行)
├── image_utils.py          # extract_image_urls + download_images + _guess_ext 等 (~200行)
├── wallpaper_crawler.py    # WallpaperItem + smart_scrape_wallpapers + 辅助函数 (~300行)
├── video_crawler.py        # VideoItem + extract_video_metadata + smart_scrape_videos (~350行)
├── douyin_crawler.py       # DouyinVideoItem + scrape_douyin_search + scrape_douyin_video + 辅助 (~300行)
└── utils.py                # 去掉重复 UA 池，保留 UserAgentPool + export_* (~已有)
```

**关键约束**：
- 所有 import 路径要同步更新
- [crawagent/tools/__init__.py](crawagent/tools/__init__.py) 的导出要更新（新文件 → 新 import）
- [crawagent/graph/workflow.py](crawagent/graph/workflow.py) 和 [crawagent/graph/agent_workflow.py](crawagent/graph/agent_workflow.py) 中的 import 也要更新
- **拆分后必须能正常运行 `python main.py`，不做行为变更**

### P1-2：统一 UA 池（消除重复）

**当前问题**：两套 UA 列表各自维护：
- `_DESKTOP_UAS` in [crawagent/tools/utils.py](crawagent/tools/utils.py)
- `_UA_POOL` in [crawagent/tools/base_crawler.py](crawagent/tools/base_crawler.py)

**修改**：
1. 合并为一个列表，放在 `utils.py` 中
2. `base_crawler.py` 的 `_build_headers()` 改为调用 `from .utils import random_user_agent`（不自己维护池子）
3. 保留 `utils.py` 中的 `UserAgentPool` 类，两处都使用同一个全局实例

### P1-3：引入 `logging` 模块替换 `print()`

**影响范围**：全项目约 79 处 `print()`。

**修改步骤**：

1. 在 [crawagent/config/settings.py](crawagent/config/settings.py) 中添加日志配置：
```python
import logging

def setup_logging(level=logging.INFO):
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
```

2. 逐个文件替换 `print()` → `logger.info()` / `logger.debug()` / `logger.warning()`：
   - 每个文件顶部 `logger = get_logger(__name__)`
   - 诊断信息 → `logger.debug()`
   - 正常流程 → `logger.info()`
   - 警告/降级 → `logger.warning()`
   - 错误 → `logger.error()`

3. **例外**：[crawagent/ui/terminal.py](crawagent/ui/terminal.py) 中的 `ConsolePrinter` 保持使用 Rich，不需要改。

### P1-4：实现 `.env` 环境变量加载

**当前问题**：[.env.example](.env.example) 自述 `.env` 未被代码实际使用。

**修改**：
1. 在 `requirements.txt` 中添加 `python-dotenv>=1.0.0`
2. 在 `load_settings()` 或 `main.py` 入口处调用：
```python
from dotenv import load_dotenv
load_dotenv()  # 自动加载项目根目录的 .env
```
3. 更新 [.env.example](.env.example)，列出所有支持的环境变量（数据库、API Key 等）

### P1-5：收紧异常处理（消除裸 `except`）

搜索以下模式并修复：

| 文件 | 行号附近 | 当前写法 | 应改为 |
|------|----------|----------|--------|
| base_crawler.py | 812 | `except Exception: continue` | `except (httpx.RequestError, Exception) as e: logger.debug(...)` |
| base_crawler.py | 1263 | `except Exception: pass` | `except (json.JSONDecodeError, KeyError) as e: logger.debug(...)` |
| agent_workflow.py | 1349 | `except Exception as e:` 后默认满意 | 区分 `TimeoutError` vs 其他异常 |
| crawler.py | 250 | `except Exception: pass` | 至少 `logger.debug()` 记录 |

### P1-6：去掉无意义的 `ThreadPoolExecutor(max_workers=1)`

**位置**：
- [agent_workflow.py:788](crawagent/graph/agent_workflow.py) — `node_decide()` 中
- [agent_workflow.py:1292](crawagent/graph/agent_workflow.py) — `node_reflect()` 中

**修改**：直接用 `llm.invoke(messages, config={"timeout": 30})` 设置超时（LangChain 支持），或者使用 `httpx` 的 timeout 参数。不要为了 timeout 开一个单线程池。

---

## 🟡 P2 — 中优先级

### P2-1：创建 `pyproject.toml`

在项目根目录创建 [pyproject.toml](pyproject.toml)：

```toml
[project]
name = "crawagent"
version = "1.0.0"
description = "智能爬虫 Agent - 多策略、多模型、配置驱动"
requires-python = ">=3.11"
dependencies = [
    "langchain>=0.3.0",
    "langchain-openai>=0.2.0",
    "langchain-anthropic>=0.3.0",
    "httpx>=0.27.0",
    "beautifulsoup4>=4.12.0",
    "requests>=2.28.0",
    "fastapi>=0.115.0",
    "uvicorn[standard]>=0.30.0",
    "pydantic>=2.0.0",
    "rich>=13.7.0",
    "prompt-toolkit>=3.0.0",
    "python-dotenv>=1.0.0",
]

[project.optional-dependencies]
browser = ["playwright>=1.40.0"]
packet = ["DrissionPage>=4.0.0"]
advanced = ["scrapling>=0.4.0", "curl-cffi>=0.7.0", "browserforge>=0.14.0", "patchright>=1.0.0"]
all = ["crawagent[browser,packet,advanced]"]

[tool.ruff]
line-length = 100

[tool.mypy]
python_version = "3.11"
strict = false
```

### P2-2：拆分 Agent 工具定义

**当前问题**：[crawagent/graph/agent_workflow.py](crawagent/graph/agent_workflow.py) 前 700 行是工具定义。

**修改**：
1. 创建 [crawagent/agent/tools.py](crawagent/agent/tools.py)，将所有 `@tool` 函数移到该文件
2. `agent_workflow.py` 只保留 State、节点函数和工作流类
3. 更新 import

### P2-3：修复内部属性访问

**位置**：[agent_workflow.py:470](crawagent/graph/agent_workflow.py)
```python
crawler2._browser_mode = True  # ❌ 直接改私有属性
```
**改为**：
```python
crawler2.set_browser_mode(True)  # ✅ 使用公开 API
```

同时把 `Crawler._browser_mode` 改为 `_browser_mode` 或提供 property。

### P2-4：为核心纯函数添加单元测试

创建 `tests/` 目录，添加以下测试文件：

```
tests/
├── __init__.py
├── test_utils.py           # UserAgentPool, export_json/csv/md
├── test_url_detection.py   # extract_urls, looks_like_url, is_video_site, is_douyin_*
├── test_anti_bot.py        # _detect_anti_bot, _need_browser
├── test_config.py          # Settings load/save/add/remove
└── test_llm_factory.py     # ModelConfig, LLMFactory init/switch
```

每个测试文件用标准 `unittest` 或 `pytest`，不需要 mock LLM 调用（测试纯函数即可）。

### P2-5：清理 `crawagent/tools/packet_crawler/` 中的冗余

**位置**：[crawagent/tools/packet_crawler/login_and_save_cookie.py](crawagent/tools/packet_crawler/login_and_save_cookie.py)

检查该文件是否被其他模块引用。如果没有，标记为"待完成"或移到单独的开发分支。

---

## 📋 执行顺序

```
第1步：P0-1, P0-2, P0-3, P0-4 （并行做，互不依赖）
第2步：P1-3 (logging)   ← 后续改动都会用到
第3步：P1-1 (拆分 base_crawler.py) + P1-2 (统一UA)  ← 一起做
第4步：P1-4 (.env) + P1-5 (异常处理) + P1-6 (线程池)
第5步：P2-1 ~ P2-5 （顺序任意）
```

---

## ⚠️ 重要约束

1. **不改行为**：除 P0 安全修复外，所有改动不应改变程序行为。改完每一步后运行 `python main.py --check-deps` 确认。
2. **不改 API 接口**：`CrawlResult`、`CrawlState`、`AgentState` 等数据类的字段不要删减。
3. **同步更新所有 import**：拆分文件时，`tools/__init__.py`、`graph/workflow.py`、`graph/agent_workflow.py`、`ui/terminal.py`、`api/server.py` 中的 import 全部要更新。
4. **不要改 CODE_WIKI.md**：该文档是手动维护的，本次改进不要求更新它。
