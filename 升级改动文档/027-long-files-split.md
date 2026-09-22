# 变更 027：长文件按子领域拆分（script_tool / settings router / sessions router）

| 项目 | 内容 |
|------|------|
| 变更编号 | 027 |
| 提出日期 | 2026-09-20 |
| 状态 | 已实施 |
| 类型 | 结构重构 |
| 关联模块 | 3 个长文件 |
| 来源 | `docs/tech-debt/2026-09-20.md` P3 #9 |

---

## 〇、价值

- **改动前**：
  - `crawagent/tools/script_tool.py` 729 行（沙箱 + venv + AST 校验 + subprocess + 输出清理）
  - `crawagent/web/routers/settings.py` 583 行（14 个 router endpoint 全堆一文件）
  - `crawagent/web/routers/sessions.py` 534 行（session CRUD + archive + history + metrics）
  - **比 AGENTS.md 推荐的 ~500 行多 50%**，改 router 不敢动怕碰错
- **改动后**：按子领域拆，每文件 < 400 行；保留外部 import 路径不变（保留 `__init__.py` 转发）

---

## 一、背景与问题

### 1.1 script_tool.py（729 行）

涉及职责：
- 沙箱配置（AST 校验）
- venv 创建/管理
- subprocess 执行 + 输出清理
- 工具注册（@tool 装饰）

### 1.2 routers/settings.py（583 行）

14 个 endpoint，5 个子领域：
- settings 模型（GET/PUT /api/settings）
- MCP server（GET/POST/DELETE /api/mcp/servers）
- background（GET /api/background）
- ecosystem（GET /api/ecosystem）
- LangSmith（GET/PUT /api/langsmith）

### 1.3 routers/sessions.py（534 行）

4 类操作：
- session CRUD（list / get / create / delete）
- archive（手动归档 + 自动）
- history（checkpoint 历史拉取）
- metrics（status / on_response）

---

## 二、目标

按职责拆，每个新文件 < 400 行；外部 import 路径保留（`from crawagent.web.routers import settings` 仍可用）。

### 2.1 script_tool.py 拆分

| 新文件 | 行（估） | 职责 |
|--------|----------|------|
| `script_tool/__init__.py` | 30 | 转发 + 注册 @tool |
| `script_tool/sandbox.py` | 200 | 沙箱配置 + AST 校验 |
| `script_tool/venv.py` | 150 | venv 创建/管理 |
| `script_tool/runner.py` | 250 | subprocess 执行 + 输出清理 |

外部 import 路径：`crawagent.tools.script_tool.run_custom_script` 仍可用（`__init__.py` 转发）。

### 2.2 routers/settings.py 拆分

| 新文件 | 行（估） | 职责 |
|--------|----------|------|
| `routers/settings/__init__.py` | 30 | 路由聚合 |
| `routers/settings/core.py` | 100 | settings 模型 GET/PUT |
| `routers/settings/mcp.py` | 150 | MCP server CRUD |
| `routers/settings/background.py` | 60 | background |
| `routers/settings/ecosystem.py` | 80 | ecosystem |
| `routers/settings/langsmith.py` | 80 | LangSmith |

外部 import 路径不变（server.py 的 `app.include_router` 不改）。

### 2.3 routers/sessions.py 拆分

| 新文件 | 行（估） | 职责 |
|--------|----------|------|
| `routers/sessions/__init__.py` | 30 | 路由聚合 |
| `routers/sessions/crud.py` | 150 | list / get / create / delete |
| `routers/sessions/archive.py` | 150 | 归档 |
| `routers/sessions/history.py` | 100 | checkpoint 历史 |
| `routers/sessions/metrics.py` | 120 | metrics |

---

## 三、方案设计

### 3.1 拆分原则

- **保留外部 API**：`from crawagent.tools.script_tool import run_custom_script` 仍可用
- **包替换**：从单文件变 package（同名）—— Python 支持（`script_tool/__init__.py` + 子模块）
- **逐个迁移**：先 script_tool，再 routers/settings，再 routers/sessions
- **每步跑 pytest**：每个文件拆完跑全量验证

### 3.2 router 拆分特殊处理

`FastAPI APIRouter` 是实例，需要在 `__init__.py` 里用 `include_router` 聚合：
```python
# routers/settings/__init__.py
from .core import router as core_router
from .mcp import router as mcp_router
api_router = APIRouter()
api_router.include_router(core_router)
api_router.include_router(mcp_router)
```

server.py 改 `from .routers.settings import api_router`（替代直接 `from .routers.settings import router`）。

### 3.3 不动

- 函数实现（纯位置重排）
- 公共 API（保留 import 兼容）
- 端点 URL（FastAPI route path 不变）

---

## 四、涉及文件清单

| 操作 | 文件 |
|------|------|
| 删除（内容搬到包） | `crawagent/tools/script_tool.py` |
| 新增包 | `crawagent/tools/script_tool/` (4 文件) |
| 删除（内容搬到包） | `crawagent/web/routers/settings.py` |
| 新增包 | `crawagent/web/routers/settings/` (6 文件) |
| 删除（内容搬到包） | `crawagent/web/routers/sessions.py` |
| 新增包 | `crawagent/web/routers/sessions/` (5 文件) |
| 修改 | `crawagent/web/server.py`（router include 方式） |

---

## 五、验证方式

1. **全量 pytest**：410 passed 不变
2. **冒烟**：`uv run crawagent start`，主路由 + 设置页 + chat 页都 200
3. **`uv run python -c "from crawagent.tools.script_tool import run_custom_script"`** 验证外部 import 仍可用

---

## 六、后续可扩展（不在本次范围）

- 028 前端测试批量补（依赖本变更的 router 结构稳定）
- 030 长函数拆（依赖本变更）

---

## 七、实施顺序建议

1. 先 script_tool（最独立）
2. 再 routers/settings（4 子领域清晰）
3. 最后 routers/sessions（与 checkpoint / metrics 耦合较多）
4. 每步跑 pytest + 冒烟

---

## 八、实施记录

**实施日期**：2026-09-22
**实施人**：Agent（指挥官 Joker 批准）

### 8.1 实际改动总览

| 原文件 | 原行数 | 拆分后 | 最大子文件行数 |
|--------|--------|--------|----------------|
| `tools/script_tool.py` | 880 | `script_tool/{__init__,header,profiles,runner}.py` | header.py 556（raw string 模板）；runner.py 189（可执行代码） |
| `web/routers/settings.py` | 719 | `settings/{__init__,core,mcp,ecosystem,background}.py` | core.py 390 |
| `web/routers/sessions.py` | 609 | `sessions/{__init__,history,crud,archive}.py` | crud.py 360 |

所有可执行代码文件均 < 400 行（spec 目标达成）。header.py 556 行是 `_ALLOWED_HEADER` raw string 模板（注入 AI 脚本的预导入头部），非可执行逻辑，不可再拆。

### 8.2 script_tool.py 拆分

| 新文件 | 行 | 职责 |
|--------|----|------|
| `__init__.py` | 21 | 转发 `run_custom_script` / `_ensure_tmp` / `DATA_TMP_DIR` / `get_settings` / `_ALLOWED_HEADER` / `session_subdir` / `subprocess`（测试 monkeypatch 兼容） |
| `header.py` | 556 | `_ALLOWED_HEADER` raw string 模板（注入 AI 脚本的预导入 + 4 helper） |
| `profiles.py` | 141 | URL 提取 + 站点档案自动注入（`_URL_RE` / `_registrable_domain` / `_extract_origins_from_code` / `_load_injected_profiles`） |
| `runner.py` | 189 | `_ensure_tmp` + `@tool run_custom_script` + 模块级路径 |

**特殊处理**：`run_custom_script` 内 `get_settings()` 改为延迟导入 `from crawagent.tools.script_tool import get_settings as _get_settings`，使测试的 `monkeypatch.setattr(script_tool, "get_settings", ...)` 生效（import 时绑定问题）。

### 8.3 settings.py 拆分

| 新文件 | 行 | 职责 |
|--------|----|------|
| `__init__.py` | 35 | 路由聚合（4 子 router include）+ re-export 共用函数 |
| `core.py` | 390 | settings 模型 GET/PUT + 模型 CRUD + env 读写 + 校验 |
| `mcp.py` | 159 | MCP server 脱敏 / `save_mcp_servers` 共用函数 / POST save endpoint |
| `ecosystem.py` | 73 | 生态快照 + 打开目录 |
| `background.py` | 96 | 背景图 CRUD |

**跨子模块引用**：`mcp.py` 从 `.core` 导入 `_save_env_updates`；`ecosystem.py` 从 `.mcp` 导入 `_mask_mcp_headers`。`parents[3]` 全改为 `parents[4]`（包目录深一层）。

**死代码清理**：原 settings.py 661-719 行是 `clear_background_route` return 后的 unreachable 代码（旧 anything-analyzer 启动函数残留），已丢弃。

### 8.4 sessions.py 拆分

| 新文件 | 行 | 职责 |
|--------|----|------|
| `__init__.py` | 26 | 路由聚合 + re-export 共用函数 |
| `history.py` | 168 | `_ARCHIVE_SUMMARY_PROMPT` / `_reconstruct_status` / `_reconstruct_messages` / GET /api/history |
| `crud.py` | 360 | 导出 / 列表 / 重命名 / 删除 / 批量删 / 上下文 |
| `archive.py` | 166 | `_archive_session_sync` / 归档 endpoint / `_background_summarize` / 指标 |

**跨子模块引用**：`crud.py` 从 `.archive` 导入 `_archive_session_sync` / `_background_summarize`；从 `.history` 导入 `_reconstruct_messages`。

### 8.5 registry.py 修改

工具自动发现从 `glob("*.py")` 改为 `iterdir()` 扫描单文件 + 包目录（`*/__init__.py`）。现有 `_template` / `assets` / `__pycache__` 目录不受影响（`_` 前缀跳过 / 无 `__init__.py`）。

### 8.6 测试适配

| 测试文件 | 改动 |
|----------|------|
| `test_web_api.py` | settings: `settings_router.ENV_FILE/get_settings` → `.core.` + `.mcp.` + `.ecosystem.`；sessions: `sessions_router.get_agent/get_checkpointer/get_meta_conn` → `.crud.` |
| `test_background_api.py` | `settings_mod._DATA_DIR/_BG_IMAGE/_BG_META/_MAX_BG_BYTES` → `.background.` |
| `test_settings_advanced.py` | `settings_mod.ENV_FILE` → `.core.ENV_FILE`；`parents[3]` → `parents[4]`（`__file__` 深一层） |
| `test_settings_langsmith.py` | `settings_mod.ENV_FILE` → `.core.ENV_FILE`（4 处） |
| `test_mcp_toggle_osenv.py` | 字符串 monkeypatch `settings.ENV_FILE` → `settings.core.ENV_FILE` |

### 8.7 验证结果

- **`uv run pytest tests/ -q`**：603 passed, 6 warnings（与基线一致，零回归）
- **import 冒烟**：`from crawagent.tools.script_tool import run_custom_script` / `from crawagent.web.routers import settings, sessions` 全部可用
- **router 聚合**：`settings.router` / `sessions.router` 是聚合 APIRouter，`app.include_router` 正常

### 8.8 实施经验

1. **包替换单文件的 monkeypatch 兼容**：测试 `monkeypatch.setattr(module, "attr", ...)` 替换的是该模块命名空间中的引用。拆包后原模块级变量（`ENV_FILE` / `get_settings` / `_BG_IMAGE` 等）分散到子模块，测试需改为 `module.submodule.attr`。import 时绑定语义使 patch 源模块无效——必须 patch 使用该变量的子模块。
2. **`__file__` 路径深度变化**：单文件 `settings.py` 的 `parents[3]` = 项目根；包 `settings/__init__.py` 的 `parents[3]` = `crawagent/`（深一层），全改 `parents[4]`。同理 `core.py` / `background.py` / `ecosystem.py` 中的 `Path(__file__).resolve().parents[N]`。
3. **registry 包发现**：`glob("*.py")` 不扫包目录，改 `iterdir()` 同时支持单文件和 `*/__init__.py`。这是包替换的必要前置修改。
4. **off-by-one 边界**：Python 切片 `lines[a:b]` 不含 index b。函数 `find_line` 返回的是 `def` 行的 0-based index，下一个函数的 index 是上一个 range 的 end（不含）。手动计算 range 时容易 off-by-one——用 `find_line` 自动定位更可靠。