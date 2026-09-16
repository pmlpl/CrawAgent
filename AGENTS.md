# AGENTS.md

CrawAgent — LLM 驱动的智能爬虫 Agent 框架。Python（FastAPI + LangGraph，包名 `crawagent`）后端 + Vue 3 前端（`web/`，产物 `web/dist` 由 FastAPI 托管）。

- 后端：`crawagent/tools/`（爬虫工具）、`crawagent/web/`（server + routers + turn_engine 事件泵）、`crawagent/graph/`（agent/middleware/skills）、`crawagent/prompts/system.md`（Agent 行为契约，改工具必同步）。
- 前端：`web/src/pages/` + `components/`；设置页是四页签结构（`components/settings/`），共享样式 `settings.css` 全部挂 `.settings-page` 前缀。
- 文档：`wiki/`（用户向使用说明）、`docs/adr/`（架构决策）、`docs/architecture/`（架构图 SVG）、`docs/agents/`（Agent 工作流）、`CONTEXT.md` 词汇表、`升级改动文档/`（编号变更规格 + 实施记录）。

## 任务汇报口令（强制）

每次任务汇报的**开头**必须先说一句：

> 报告！我已完成指挥官Joker指示。

这是指挥官确认新会话已加载本文件的标记。漏说 = 没读到，视为会话未刷新。说完这句再接正文汇报。

## 工作方式（指挥官规矩，违反会被打回）

- 步进式指挥：单一指令只做被指示那步并汇报；批量/编号需求整批做完（实现+测试+build+提交）再逐项汇报；**报 bug = 授权修复**；"我自己动手改" = 只给文件行号+改法不动手。
- 改动流程：非琐碎改动先在 `升级改动文档/` 写编号规格（005 起递增）待批准，完成后补「实施记录（人话版）」随代码入库；难逆决策落 `docs/adr/`，新术语进 `CONTEXT.md`。
- 提交：中文一行式主题 + 要点正文；先 `uv run pytest tests/ -q` 与 `cd web && npm test` 全绿；**git add 只列明确路径**（多会话并行写同一仓库，禁 add -A）；`HANDOFF-*.md` 不提交。

## 常用命令

```bash
uv run pytest tests/ -q              # 后端全量测试
cd web && npm test && npm run build  # 前端测试与构建（改动必须 build，否则误判"没实现"）
uv run crawagent start               # 启动（改了 Python 必须重启）
uv run python -m crawagent.web.server  # 临时冒烟用这个（静态托管在 main() 里，直接起 uvicorn app 会 404 /assets）；配 CRAWAGENT_HOST=0.0.0.0 避免弹浏览器
```

## 架构边界与层级规则

- **工具自动发现**：`crawagent/tools/*.py` 里的 `@tool` 函数经 registry 自动收集；`_TOOL_META` 补 category/deps；`registry.py` 是单向依赖底座，绝不 import 上层模块；`_SKIP_TOOL_NAMES` 里的动态装配工具（MCP 等）不进扫描。
- **system.md 是契约**：新增/改动工具必须同步工具清单、条数与相关硬规则（BATCH-FIRST、ASK-USER、脚本阶梯等），否则 Agent 行为与文档脱节。
- **事件链路**：轮次事件经 `turn_engine._emit` 落 EventLog（重连从 0 重放）+ 队列；工具需要发"可重放 UI 事件"（如 ask_user 选择题）走 `progress.emit_turn_event`，不要直接 import web 层。
- **需要用户授权/决策（拉起服务、批量下载、删改文件）必须走 `ask_user` 选择题，任何时刻禁止静默自动拉起服务**（MCP_AUTOSTART 已废除）。
- **前端状态层是模块级单例组合式**（useSettings/useBg/useChat）：跨页共享，设置页 onMounted 会清残留提示；新组件直接取用，别 props 钻透。

## 已知坑（踩过的，别再踩）

- **.env 可空设置落 `null`**：依赖 Settings 的 `env_parse_none_str="null"`（pydantic-settings 默认把 null 当字符串，int 字段直接崩掉后端启动）；必填数值清空必须服务端拒绝，不能落盘 `None`。
- **冒烟先清端口**：webapp-testing 的 with_server 杀不干净 uvicorn 孤儿进程，后续冒烟会打在旧代码上；结果与代码预期不符时第一反应查残留进程（`netstat -ano | grep :<port>`）。
- **编辑工具吞尖括号**：含 `<>` 的代码用 Python 脚本落盘，别直接写进 Edit/Write。
- 背景图/壁纸存服务端 `data/`（ADR-0001），localStorage 已弃用；产物目录在高级页可编辑、保存即生效（工具层每次现读 get_settings()）。
- 改动后基线：329 pytest + 10 vitest 全绿（工具/设置再扩展时同步更新计数测试与 `crawagent/prompts/system.md` 工具清单 + `wiki/03-工具与技能总览.md` 的工具数）。

## Agent skills

### Issue tracker

GitHub issues (repo: pmlpl/CrawAgent). Use `gh` CLI for all operations. See `docs/agents/issue-tracker.md`.

### Triage labels

Default five canonical triage roles. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: `CONTEXT.md` + `docs/adr/` at repo root. See `docs/agents/domain.md`.
