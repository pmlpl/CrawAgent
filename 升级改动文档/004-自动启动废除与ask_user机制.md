# 变更 004：废除 MCP 自动启动 + 通用 ask_user 询问机制

| 项目 | 内容 |
|------|------|
| 变更编号 | 004 |
| 提出日期 | 2026-09-06 |
| 状态 | 已完成（2026-09-07） |
| 类型 | 行为调整 + 功能增强 |
| 关联模块 | crawagent/tools/ask_user_tool.py（新增）、progress.py、turn_engine.py、server.py、mcp_capture_tool.py、graph/skills.py、config/settings.py、useChat.js、AskCard.vue |

---

## 〇、价值

- **改动前**：启动 crawagent 时 anything-analyzer 被拉起**两次**；MCP 服务是否启动完全由系统自作主张，用户对"AI 背后开了什么"没有掌控。
- **改动后**：任何服务都不会被静默启动。AI 需要时在聊天页弹一张选择题卡片（如「要打开 anything-analyzer 吗？〔打开〕〔暂不〕」），用户点按钮作答，AI 拿着答案继续。这套机制适用于**一切需要用户授权/决策的场景**（批量下载、删改文件、要 Cookie）。

## 一、背景与问题

1. **双开**：启动 crawagent 时 anything-analyzer 开两个实例。
2. **自主性越界**：自动启动是早期"AI 自己拉不起 anything"年代的垫路砖（设置页一堆提示与按钮都是那批遗产），原生拉起打通后已无必要。
3. **询问无反馈闭环**：AI 用纯文字问用户"要不要打开"，既没有结构化答案，用户也不知道点了什么、AI 收到了什么。

## 二、目标

1. 自动启动全链拆除：设置字段、门控、**Agent 构建期的拉起调用**、路由快照与保存分支、前端开关——任何时刻不静默拉起服务。
2. 通用 ask_user 机制：AI 侧一个阻塞工具，用户侧聊天页按钮，答案结构化回到 AI；刷新页面按钮仍在。
3. system.md 立硬规则：需要授权/决策必须 ask_user；超时视为暂缓，同轮不许原样重问；MCP 未运行必须先问后开。
4. **不做**：WebSocket 断线期间的异步问题队列；多问题并行；选择按钮以外的输入形态（自由文本、滑杆）。

## 三、方案设计

- **工具即阻塞**：`ask_user(question, options, timeout=300)` 在轮次线程内 `threading.Event.wait()` 阻塞——AI 的一次工具调用天然就是"等用户"。
- **问题可重放**：问题经 progress.py 新增的轮次事件出口（`emit_turn_event`）推进事件日志（type=ask）。事件日志在页面刷新/重连时全量重放，所以按钮不会因为刷新而消失。事件出口用 ContextVar（graph.agent 的 ctx_session_id）定位当前会话——工具线程与轮次同线程，注册/注销由 turn_engine 在轮次起止负责。
- **答案回传**：用户点击 → 前端 WS 发 `{type:"ask_answer", ask_id, value}` → server.chat_ws 新分支 → `resolve_ask` 写答案、set 唤醒 → 工具把所选原文当普通工具结果返回给 AI。重复回答/未知 ask_id 幂等忽略。
- **超时闭环**：等待超时返回"视为暂缓"提示语，并推 `ask_answered(value=null)` 事件让 UI 锁定置灰；AI 被要求继续做无需授权的部分。
- **自动启动拆除**：删除 MCP_AUTOSTART 设置字段与门控；**关键是拆掉 Agent 构建路径里的无条件拉起**（见实施记录）；ensure_mcp_started 只在显式调用（check_mcp_status force 路径）时执行。

## 四、涉及文件清单

| 操作 | 文件 | 说明 |
|------|------|------|
| 新增 | crawagent/tools/ask_user_tool.py | ask_user 工具 + resolve_ask + 等待注册表 |
| 修改 | crawagent/tools/progress.py | 轮次事件出口（set/clear/emit_turn_event） |
| 修改 | crawagent/web/turn_engine.py | 轮次起止注册/注销事件出口 |
| 修改 | crawagent/web/server.py | WS ask_answer 分支 |
| 修改 | crawagent/tools/mcp_capture_tool.py | 拆除构建期自动拉起（双开根因） |
| 修改 | crawagent/config/settings.py、graph/skills.py、web/routers/settings.py | MCP_AUTOSTART 字段/门控/快照与保存分支删除 |
| 新增 | web/src/components/AskCard.vue | 选择题卡片（点击高亮、超时置灰） |
| 修改 | web/src/composables/useChat.js + 测试、pages/ChatPage.vue | ask/ask_answered 事件、answerAsk、卡片挂载 |
| 修改 | crawagent/prompts/system.md | ask_user 工具条目 + ASK-USER RULE + MCP 先问后开 |

## 五、验证方式

1. ask_user 单元：用户点选后返回所选原文并推 ask/ask_answered 两事件；超时返回暂缓语且注册表无悬挂；选项数非法被拒；resolve 幂等。
2. 前端 vitest：ask 事件生成卡片、answerAsk 走 WS、ask_answered 锁定/置灰。
3. 全量 pytest / vitest / build 绿；Agent 构建通过（内置工具 21→22）。
4. 真机行为：启动 crawagent 不再出现 anything-analyzer 弹窗；AI 需要抓包分析时聊天页出现选择卡。

## 六、后续可扩展（不在本次范围）

- 断线/重启后的异步问题队列（问题持久化、跨轮回答）；自由文本与文件上传形态；一次多题并行。

## 七、实施顺序建议

后端机制（事件出口→工具→WS 分支）→ 测试 → 前端事件与卡片 → system.md 规则 → 自动启动拆除 → 全量验证。

---

## 八、实施记录（2026-09-07）

### 怎么做到的（人话版）

**双开的真凶不在启动脚本，在 Agent 构建路径。** 排查时按直觉先查了启动脚本和设置开关，都没有重复调用的痕迹；最后发现装配 MCP 工具箱的代码里有一句"构建时顺带确保服务就绪"——只要 Agent 被构建（启动预热、首轮对话都会触发），就会拉起一次 anything-analyzer，加上其他显式触发点，一次启动就弹两个窗口。教训：**"什么时候会执行"比"执行了什么"更该先查**。拆除方式也顺理成章：工具装配只装配，绝不启动服务；服务没起时它的工具本轮装不进来，等 AI 问过用户、用户点头后再拉起并重建工具箱。

**ask_user 的关键取舍是"阻塞"而不是"异步消息"**。另一个做法是 AI 发完问题就结束本轮、用户回答后开新一轮——但那样 AI 会丢掉手头的中间状态，恢复上下文的成本极高。改成阻塞后，AI 的一次工具调用就是"等用户点头"，工具内部用一个线程事件挂起，拿到答案醒来把选项原文当普通结果交回——对 AI 和对框架都只是"这个工具跑了 30 秒"，零特殊处理。代价是这一轮不能干别的（本来就是顺序执行），以及用户长时间不答会占住线程，所以默认 300 秒超时、上限 1800。

**"刷新页面按钮还在"靠的是走事件日志而不是临时推送。** 项目已有的事件日志机制本来是为了"done 事件不被 WebSocket 断线吞掉"设计的（历史 bug 的遗产）：每条事件落日志，重连从 0 重放。ask 事件直接复用这条管道——刷新后按钮重放出来，还能点，答案照常唤醒后端挂着的线程。工具要往这条管道里发事件，需要知道"当前是哪个会话、事件往哪投"：轮次开始时把投递函数登记进 progress 模块的注册表（用 ContextVar 里的会话 ID 对号），轮次结束注销。工具层因此不需要 import 网页层，保持零循环依赖。

### 实际改动

与规格一致，无偏差。内置工具 21→22（ask_user）；测试基线 159 pytest + 7 vitest。

### 验证结果（对照 §五）

1-3 ✓：5 个 ask_user 单元（点选回传、超时闭环与清理、非法输入、幂等、无轮次安全）+ 7 vitest（含 ask 事件与 WS 回传断言）+ 全量 159 pytest 绿 + Agent 构建 23 工具（22 内置 + 1 插件）。
4 ✓ 启动无弹窗（自动启动链路已不存在）；真机选择卡待 AI 下次实际发起时验收。

### 说明

- 本变更随 commit `f965ead`（自动启动废除）与 `cdbef07`（ask_user 机制）入库；MCP 区历史脚手架的前端拆除随 `ca88b2c`。
- .env 里遗留的 `MCP_AUTOSTART=true` 行已无效（配置 extra=ignore），可自行删除。
- 机制说明沉淀长期记忆（ask-user-mechanism）；后续新场景（批量下载授权等）在 system.md 补引导即可，机制无需再动。
