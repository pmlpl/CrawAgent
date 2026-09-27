# 变更 032：浏览器子 Agent 模型设置与步进进度

| 项目 | 内容 |
|------|------|
| 变更编号 | 032 |
| 提出日期 | 2026-09-25 |
| 状态 | 已完成（2026-09-27） |
| 类型 | 功能增强 |
| 关联模块 | crawagent/config/settings.py、crawagent/web/routers/settings/core.py、crawagent/tools/advanced_tools.py、crawagent/tools/progress.py、web/src/components/settings/SettingsModels.vue |

---

## 〇、价值

- **改动前**：`browser_use_navigate` 的浏览器小 Agent 只能用主 LLM（带思考、每步慢），想换便宜快模型必须手写 `.env` 环境变量（`BROWSER_USE_LLM_API_KEY/MODEL/BASE_URL` 三档）；且运行期间 Web 端 trace 卡片全程静止（只显示"运行中"，0/N 不动），终端刷 browser-use 步进日志但页面看不出任何进展——实测一加官网抓机型任务跑几十分钟，页面像卡死。
- **改动后**：设置页「模型」页签新增「浏览器子 Agent」卡片，配便宜快模型保存即生效；trace 卡片实时显示"步骤 N：动作摘要"里程碑行 + 自动"已耗时 Xs"心跳，与其它长耗时工具一致的实时体验。

## 一、背景与问题

1. 009 引入的 `browser_use_navigate`（advanced_tools.py:159）用 browser-use 0.13 启动可见 Chromium + LLM Agent 循环（默认最多 100 步，每步一次 LLM 调用），交互任务天然耗时数分钟到数十分钟。
2. 该工具**不在** `progress.LONG_RUNNING_TOOLS` 名单（progress.py:308），agent.py:79 只对名单内工具套 `with_progress` 包装器，所以连自动"运行中… 已耗时 Xs"心跳都没有；工具内部也没有调 `report_progress`。这就是"终端热闹、Web 端静止"的根因。
3. 换模型的三档逻辑（独立 key / 复用 provider 换模型 / 主 LLM）只存在于工具代码注释里，靠手写 `.env`，无 UI、无脱敏回显。
4. **视觉能力实测（2026-09-25，发纯色图验证）**：当前主模型 glm-5.2 收到图片不报错但内容没进上下文（纯绿图答空、纯红图答"黑色"），即 browser-use 默认 `use_vision=True` 发的每步截图实际"没人看"，Agent 靠 browser-use 消息里的 DOM 结构文本盲操作——能跑，但复杂页面定位靠猜。GLM-5.2/5.3 是文本线模型，选子 Agent 模型必须选**支持图片输入**的视觉模型（GLM-4V / GLM-5.1 视觉线等），否则换模型只提速不提准。

链路盘点（都是现成的，本变更只补"最后一公里"）：
- `report_progress()` 里程碑 → turn_engine WS 循环每 ~2s `consume_new()`（turn_engine.py:559）→ `{"type":"progress"}` 事件 → 前端 useChat.js:366 `case 'progress'` → CrawlTrace 卡片内联日志。**前端 trace 侧零改动。**
- browser-use 0.13 原生支持 `register_new_step_callback: Callable[[BrowserStateSummary, AgentOutput, int], None|Awaitable]`（agent/service.py:154），每步模型输出后回调，同步/异步均可。
- 设置页后端有 010 LangSmith 先例可照抄：Settings 字段 → `_settings_snapshot`（`_mask_key` 脱敏）→ save 分支（含 `*` 跳过）→ 前端 card。

## 二、目标

1. 设置页「模型」页签新增「浏览器子 Agent」配置卡片：模型名 / Base URL / API Key 三输入；**全部留空 = 跟随主模型**（现状行为）；API Key 脱敏回显，含 `*` 跳过更新。
2. 后端 `Settings` 新增三个可空字符串字段 + snapshot 暴露 + save 分支（照 010 模式）；工具层读取优先级 `get_settings()` > `os.environ`（向后兼容已手配的用户）> 主 LLM；`get_settings()` 非缓存现读，**保存即生效无需重启**。
3. `browser_use_navigate` 接入步进进度：加入 `LONG_RUNNING_TOOLS` 名单（自动获得 `with_progress` 心跳）+ `register_new_step_callback` 回调内 `report_progress("步骤 N：动作摘要")`。
4. **不做**：headless 开关（维持可见浏览器）、task 拆小引导、`max_steps` 上限配置、`crawl4ai_deep_crawl` 的进度接入（记入 §六）。

## 三、方案设计

### 3.1 Settings 字段（config/settings.py）

紧挨 LangSmith 字段区域（:162 附近）新增：

```python
browser_use_llm_model: str = ""
browser_use_llm_base_url: str = ""
browser_use_llm_api_key: str = ""
```

### 3.2 snapshot 与 save（web/routers/settings/core.py）

- `_settings_snapshot` 加三键：`browser_use_llm_model`、`browser_use_llm_base_url` 直出；`browser_use_llm_api_key` 过 `_mask_key`。
- save 分支：model / base_url 直写 `.env`（空串 = 清除，恢复跟随主模型）；api_key 含 `*` 跳过（保留旧值，010 语义）。
- **无 os.environ 盖住风险**：这三个键启动期不会被写进 os.environ（不同于 MCP_SERVERS 的 auto_sync），且工具层读 `get_settings()` 不经 os.environ，纯 `.env` 落盘即可生效。

### 3.3 工具层三档改造（tools/advanced_tools.py）

`browser_use_navigate` 的 LLM 构造段改为：

1. `get_settings().browser_use_llm_model` 非空 → 视为配置了独立子 Agent LLM：model 用该值，base_url / api_key 为空时**回落主 provider 对应值**（与现第 2 档"复用 provider 换模型"语义一致，只填模型名即可用）；
2. settings 为空 → `os.environ` 的 `BROWSER_USE_LLM_*` 兜底（现逻辑原样保留，兼容已手配用户）；
3. 都无 → 主 LLM（现状）。

`_ensure_no_proxy_for` 对最终 base_url 照常调用。

### 3.4 步进进度（tools/advanced_tools.py + tools/progress.py）

- `progress.py` 的 `LONG_RUNNING_TOOLS` 加 `"browser_use_navigate"`（一行，自动心跳）。
- Agent 构造加 `register_new_step_callback=_on_step`；同步回调 `_on_step(state, output, step_no)`：
  - 动作摘要从 `output.action[0]` 提取（`model_dump()` 第一个非空键 + 关键参数，截断 ~60 字），格式 `f"步骤 {step_no}：{摘要}"`；
  - 摘要取不出时退化为 `f"步骤 {step_no} 执行中"`；
  - 整个回调 try/except 静默吞异常（进度上报失败绝不影响主流程）。
- 回调由 browser-use 的事件循环线程调用；`report_progress` 内部 `_LOCK` 线程安全，无需额外处理。
- **用 `report_progress`（轨迹行）而非 `emit_turn_event`（可重放 UI 状态事件）**：步进日志是过程性信息，不需要刷新重放，且 report_progress 走现成心跳通道零新事件类型。

### 3.5 前端卡片（web/src/components/settings/SettingsModels.vue）

- 「模型」section 之后新增「浏览器子 Agent」card：说明行（"驱动 browser_use_navigate 的浏览器小 Agent；留空 = 跟随主模型。**须选支持图片输入的视觉模型**（如 GLM-4V / GLM-5.1 视觉线），纯文本模型会退化为按 DOM 文本盲操作"）+ 三输入（模型名 / Base URL / API Key，key 占位符按 010 脱敏回显语义）+ 保存按钮。
- 加载时从 settings 快照回显；保存调现有 settings 保存端点；成功提示沿用页面现有模式。
- 样式沿用 settings.css 现有 `card`/`field` 类（已挂 `.settings-page` 前缀），不新增全局 CSS。
- 无 on/off 开关，不涉及滑动 switch 偏好。

### 3.6 测试

- 后端（tests/）：Settings 三字段解析；snapshot 脱敏；save 分支（含 `*` 跳过、空串清除）；工具 LLM 构造三档优先级（settings 优先 / environ 兜底 / 主 LLM）；步进回调 → `report_progress` 里程碑（mock output）+ 回调异常不冒泡。
- 前端：SettingsModels 新卡片渲染 + 保存流程（照 SettingsAppearance.test.js 模式）。
- 基线计数同步：603 pytest + 43 vitest → 实施后实际数。

## 四、涉及文件清单

| 操作 | 文件 | 说明 |
|------|------|------|
| 修改 | crawagent/config/settings.py | +3 可空字段 |
| 修改 | crawagent/web/routers/settings/core.py | snapshot 三键（key 脱敏）+ save 分支 |
| 修改 | crawagent/tools/advanced_tools.py | LLM 三档优先级 + register_new_step_callback |
| 修改 | crawagent/tools/progress.py | LONG_RUNNING_TOOLS +browser_use_navigate |
| 修改 | web/src/components/settings/SettingsModels.vue | 「浏览器子 Agent」卡片 |
| 修改 | tests/ 相应文件 | 后端 + 前端新测试、基线计数 |

## 五、验证方式

1. `uv run pytest tests/ -q` 全绿（603 + 新增）。
2. `cd web && npm test && npm run build` 全绿（43 + 新增）。
3. 设置页填三字段保存 → `.env` 落盘三键；刷新页面 API Key 脱敏回显；含 `*` 时保存不覆盖旧值；清空模型名保存 → 恢复跟随主模型（`.env` 键清空）。
4. 重启后发起浏览器任务（如"打开 example.com 并点击页面上的链接"）：trace 卡片出现"步骤 N：…"里程碑与"已耗时 Xs"心跳；浏览器窗口照常弹出可见。
5. 模型名填便宜快模型（如 glm-5.3-flash）实测单步耗时明显缩短（人工感知，不硬断言）。

## 六、后续可扩展（不在本次范围）

- `crawl4ai_deep_crawl` / `markitdown_convert` 加入 LONG_RUNNING_TOOLS（前者一行即得心跳）。
- 视觉能力探测/自动降级：子 Agent 模型不支持图片输入时自动 `use_vision=False`（browser-use 对 DeepSeek 已有同款先例）+ 日志提示，省掉无效截图 token。
- `max_steps` / 超时上限做成设置项，防浏览器 Agent 失控空转。
- headless 开关做成设置项（配合"围观模式"偏好）。
- 步进动作摘要带元素定位信息（更可读）。

## 七、实施顺序建议

后端字段 + snapshot/save → 工具层三档 + 步进回调 → 后端测试 → 前端卡片 + 测试 + build → 端到端验证 → 实施记录。

---

## 八、实施记录（2026-09-27）

### 怎么做到的（人话版）

浏览器小 Agent 换模型这件事，管道早就铺好了——Settings、.env 写入、脱敏回显、设置页卡片全是现成模式，照 010 LangSmith 的先例搬过来就行。真正的新活有三件：

1. **LLM 三档优先级**：设置页三字段（`browser_use_llm_model/base_url/api_key`）非空模型名即视为手配，base_url / key 留空时回落主 provider 对应值（只填模型名即可复用现有服务商）；settings 空则走老的 `os.environ` 兜底（原样保留），再空才是主模型。有个测试阶段才发现的暗坑：pydantic-settings 会把 `os.environ` 的 `BROWSER_USE_LLM_*` 自动读进同名 Settings 字段——老用户手写 .env 的配置其实无缝迁进了第一档，"设置档压过 env 档"这类测试前提根本不成立，两档天然合并，测试改成忠实记录这个合并语义。
2. **步进里程碑**：模块级 `_on_browser_step` 回调挂在 `register_new_step_callback` 上，从 `output.action[0].model_dump()` 抠第一个有效动作键 + 参数拼"步骤 N：动作摘要"（60 字截断；`done: {"text": ""}` 这类全空字典也算取不出，退化为"步骤 N 执行中"）；整个回调两层 try/except，进度上报失败绝不碰主流程。走 `report_progress` 轨迹行（非 emit_turn_event），前端 trace 卡片零改动。
3. **心跳补位**：`browser_use_navigate` 加进 `LONG_RUNNING_TOOLS`，agent.py 自动套 `with_progress`——就算回调哑了也还有"运行中… 已耗时 Xs"兜底心跳。

设置页「浏览器子 Agent」卡片三输入 + 保存按钮，key 脱敏回显（含 `*` 不回写）、清空保存 = 清除（三键全空恢复跟随主模型）。保存即生效：get_settings() 不缓存，工具层每次现读。

### 实际改动

| 操作 | 文件 | 说明 |
|------|------|------|
| 修改 | crawagent/config/settings.py | +3 可空字段 browser_use_llm_model/base_url/api_key |
| 修改 | crawagent/web/routers/settings/core.py | snapshot 三键（key 过 _mask_key）+ save 三分支（掩码跳过/空串清除） |
| 修改 | crawagent/tools/advanced_tools.py | LLM 三档优先级 + `_on_browser_step`/`_meaningful` 步进回调 + Agent 挂 register_new_step_callback |
| 修改 | crawagent/tools/progress.py | LONG_RUNNING_TOOLS + browser_use_navigate |
| 修改 | web/src/composables/useSettings.js | state.browserAgent 三键 + load() 回显 |
| 修改 | web/src/components/settings/SettingsModels.vue | 「浏览器子 Agent」卡片（三输入 + 保存 + 脱敏提示） |
| 修改 | tests/test_web_api.py | 两处 SimpleNamespace 假 settings 补三字段 |
| 新增 | tests/test_settings_browser_use.py | 6 用例（snapshot 脱敏/save 三分支/partial 不误清） |
| 新增 | tests/test_browser_subagent.py | 12 用例（三档优先级/no_proxy 记录/回调摘要/退化/截断/吞异常） |
| 新增 | web/src/components/settings/SettingsModels.test.js | 4 用例（卡片渲染/掩码回显/保存提交三键/失败提示） |

### 验证结果（对照 §五）

1. `uv run pytest tests/ -q` → **621 passed**（603 基线 + 18 新增）✓
2. `cd web && npm test && npm run build` → **47 passed**（43 基线 + 4 新增）+ build 成功 ✓
3. 保存/脱敏/清除链路由 HTTP 级测试全覆盖（真实 .env 不动）：三字段落盘 ✓、key 掩码回显 `****1234` 且明文不出 ✓、含 `*` 保存不覆盖原值 ✓、三键清空保存恢复跟随主模型 ✓
4. 重启后真实浏览器任务看步进里程碑与心跳——**待人工冒烟**（需 Playwright 浏览器 + LLM 链路，与 §五.5 同属人工感知项）
5. 便宜快模型提速感知——**待人工冒烟**

### 说明

- 与规格偏差一处：api_key 空串语义为**清除**（010 LangSmith 是"空 = 不改"）——本卡片的头号契约是"三键全空 = 跟随主模型"，清空 key 必须能落盘；掩码 `*` 跳过的保护语义不变。
- os.environ 的 `BROWSER_USE_LLM_*` 经 pydantic 自动并入 Settings 同名字段，老"手写 .env"用户配置无缝生效，tier2 分支保留纯兜底。
- 本变更已随本 commit 入库（代码与文档同批）。
