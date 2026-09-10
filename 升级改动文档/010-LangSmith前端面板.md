# 010 — LangSmith 前端面板

## 背景

P2-8 LangSmith 面板集成。后端早已铺好（`crawagent/config/settings.py:135-162`：`langsmith_api_key`/`langsmith_project`/`langsmith_tracing` 三字段 + `get_settings()` 用 `os.environ.setdefault` 导出 `LANGSMITH_*` env，导出以 key 非空为守门），但**前端零 UI**、**`post_settings_route` 不接 langsmith_* 分支**、**`_settings_snapshot()` 不暴露这三字段**。本变更补齐前后端缺口，让用户在设置页配置 LangSmith 追踪。

## 设计决策（grill-with-docs 共识）

1. **放「高级」tab 末尾加一张 card**（不新开第 5 tab——三字段不值得单 tab，高级页语义贴合「调试/可选」）。
2. **纯配置面板 + 重启提示**：保存写 `.env`，不引入「实时进程状态」端点。langchain 运行时在进程启动时读 env，保存后**必须重启 `crawagent start` 生效**——跟 LLM key/思考深度同款处理，保存 tip 文案体现。
3. **前端校验**：勾「启用追踪」时若 API Key 为空，tip 报红阻止保存（后端守门兜底，但早报优于静默无效）。
4. **API Key 脱敏**沿用全仓「含 `*` 即掩码」约定（前后端统一判断：含 `*` 视为掩码不回写）。
5. **不重置 agent cache**：tracing 是 langchain 运行时读 env，非工具/Agent 构建期层，`reset_agent_cache` 无关；只需 `get_settings.cache_clear()` 让 Settings 单例重读 `.env`。

## 改动清单

### 后端 `crawagent/web/routers/settings.py`

- **`_settings_snapshot()`（约 78-108 行）**：暴露三字段——
  ```python
  "langsmith_api_key": _mask(s.langsmith_api_key),
  "langsmith_project": s.langsmith_project,
  "langsmith_tracing": s.langsmith_tracing,
  ```
  （`_mask` 复用现有 key 脱敏函数；若 snapshot 现无统一 `_mask`，沿用 LLM key 同款掩码逻辑：非空返回 `****`+末4位，空返回 `""`。）
- **`post_settings_route()`（252-319 行）**：加 langsmith 分支——
  - `langsmith_api_key`：含 `*` 跳过（掩码不回写），否则写 `LANGSMITH_API_KEY`
  - `langsmith_project`：写 `LANGSMITH_PROJECT`
  - `langsmith_tracing`：bool → `"true"`/`"false"` 字符串写 `LANGSMITH_TRACING`
  - 进 `updates` dict，走现有 `_save_env_updates(updates)`
- **保存后**：调 `get_settings.cache_clear()`（让单例重读 `.env`），返回 `{"ok": True, "saved": [...]}`

### 前端 `web/src/composables/useSettings.js`

- `state`（7-41 行）加 block：`langsmith: { apiKey: '', project: 'crawagent', tracing: false }`
- `load()`（74-100 行，约 94 行后）赋值：
  ```js
  state.langsmith = {
    apiKey: data.langsmith_api_key || '',
    project: data.langsmith_project || 'crawagent',
    tracing: !!data.langsmith_tracing,
  }
  ```
- 复用现有 `saveSettingsFields(payload)`（306-313 行），不新增导出

### 前端 `web/src/components/settings/SettingsAdvanced.vue`

末尾加一张 card，镜像现有「抓取行为」card（72-93 行）模式：

- `<section class="card"><h2 class="card-title">LangSmith 追踪</h2>` + 三字段：
  - API Key：`<input type="password">`，`v-model="state.langsmith.apiKey"`，掩码态显示 `****xxxx`
  - 项目名：`<input type="text">`，`v-model="state.langsmith.project"`，hint「LangSmith 项目名，默认 crawagent」
  - 启用追踪：`<input type="checkbox">`，`v-model="state.langsmith.tracing"`，hint「需填 API Key，保存后重启 CrawAgent 生效」
- 本地 `ref` tip + saving 标志（不污染全局 `state.saveTip`，注释见 useSettings.js:304-305）
- `saveLangsmith()`：
  1. 防重入 `if (savingLang.value) return`
  2. **前端校验**：`if (state.langsmith.tracing && !state.langsmith.apiKey.trim())` → tip 报红「追踪需先填 API Key」并 return
  3. payload：`{ langsmith_api_key: state.langsmith.apiKey, langsmith_project: state.langsmith.project, langsmith_tracing: state.langsmith.tracing }`（key 含 `*` 时仍传，后端会跳过——保持与 LLM key 一致）
  4. `await saveSettingsFields(payload)` → 本地 tip 映射
  5. 成功 tip：「已保存，重启 CrawAgent 后追踪生效」
- 共享样式无需改（`settings.css` 已挂 `.settings-page` 前缀覆盖 card/field/actions）

## 测试

- 后端：`tests/test_settings_langsmith.py`（或加进现有 settings 测试）
  - `test_snapshot_exposes_langsmith`：GET /api/settings 返回含三字段
  - `test_save_langsmith_writes_env`：POST 三字段 → `.env` 出现 `LANGSMITH_*`
  - `test_save_langsmith_masked_key_skipped`：POST 含 `*` 的 key → 不回写
  - `test_save_clears_cache`：保存后 `get_settings.cache_clear` 被调
- 前端：vitest 不强求（现有 settings 组件无前端测试先例，与同页其他 card 一致）

## 不做

- 不加 test-connection 按钮（LangSmith 无轻量 ping，跑 trace 要发一轮对话）
- 不加「实时进程状态」端点（跟 LLM key 同款写 .env + 重启提示）
- 不改 system.md（LangSmith 是配置非 Agent 工具/契约）
- 不迁 `langsmith_tracing` 字段位置（虽在 settings.py:148 与 135-136 分离，但功能无碍，纯整洁不在本变更范围）

## 验收

- 设置页「高级」tab 末尾见 LangSmith card，三字段可填可存
- key 掩码正确（回显 `****xxxx`，含 `*` 不回写）
- 勾追踪但 key 空 → 报红阻止保存
- 保存成功后 tip 提示重启
- 基线 214 pytest + 10 vitest 全绿（+4 新测试 → 218 pytest）

## 实施记录（人话版，2026-09-10，commit f93c27d）

实际 +6 测试（非规格预估的 +4）：拆出掩码回显、空 key 不覆盖两用例，共 220 pytest。

- **后端**：`_settings_snapshot` 末尾加三字段 + 新增 `_mask_key` helper（空→""，否则 `****`+末4位）。`post_settings_route` 在数值表循环后加 langsmith 分支：`langsmith_api_key` 含 `*` 跳过、`langsmith_project` 默认 crawagent、`langsmith_tracing` bool→`"true"`/`"false"` 字符串。保存后 `get_settings.cache_clear()` 用 hasattr 守卫（`get_settings` 实际无 lru_cache，与 MCP save 路径同款兜底，未来若加缓存即生效）。
- **前端**：`useSettings.state` 加 `langsmith` block；`load()` 从 snapshot 赋值。`SettingsAdvanced.vue` 末尾加 card（password 输入框 + 项目名 + checkbox），本地 `langTip`/`savingLang` 避免跨卡串显，`saveLangsmith()` 前端校验「勾追踪但 key 空」→报红阻止，保存成功提示「重启 CrawAgent 后追踪生效」。
- **关键认知**：`get_settings()` 非 lru_cache，每次现读 .env；但 langchain 运行时在**进程启动**时读 `LANGSMITH_*` env（`get_settings` 用 `os.environ.setdefault` 导出，只设不覆盖），所以保存 `.env` 后跑着的进程不会热生效——必须重启。这与 LLM key/思考深度同款，UI 用重启 tip 体现，不引入「实时进程状态」端点。
- **踩坑**：测试 fixture monkeypatch `settings_mod.ENV_FILE` 不够——`Settings.model_config.env_file` 钉死项目根 `.env`（line 47），且 pydantic env vars 优先于 .env。fixture 须额外 `setitem(Settings.model_config, "env_file", tmp)` + 清 `os.environ` 的 `LANGSMITH_*`，snapshot 值断言才准。`get_settings` 的 `os.environ.setdefault` 会污染进程 env，故 roundtrip 测试不可靠（保存后 os.environ 持旧值），改全文件级断言（与 `test_settings_advanced` 同款）。
- **回归**：`test_web_api` 两个 `SimpleNamespace` 桩缺 langsmith 字段 → AttributeError，补上；`test_settings_get_does_not_leak_api_key` 的 `"api_key" not in json.dumps(body)` 过宽（`langsmith_api_key` 命中子串），收窄到 `body["providers"]`。
- 基线 220 pytest + 10 vitest 全绿。
