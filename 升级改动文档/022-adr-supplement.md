# 变更 022：补写 4 篇 ADR（sync/async 决策、checkpoint 选型、registry 装饰器、system.md 契约）

| 项目 | 内容 |
|------|------|
| 变更编号 | 022 |
| 提出日期 | 2026-09-20 |
| 状态 | 待批准 |
| 类型 | 文档 |
| 关联模块 | `docs/adr/` |
| 来源 | `docs/tech-debt/2026-09-20.md` P4 #15 |

---

## 〇、价值

- **改动前**：`docs/adr/` 只 3 篇：0001 wallpaper 服务端 / 0002 pagination v1 / 0003 MCP 无命令白名单。几件关键架构决策**没沉淀**：
  - LangChain 1.x `run_in_executor(None, ...)` 已经自动 offload 同步工具 —— 为什么没专门工具做 async 包装（016 撤档教训）
  - LangGraph checkpoint 选 SQLite（base）+ Redis（dist opt），而不是 Postgres / MySQL —— 选型理由
  - `@tool` + `@register_tool` 装饰器分工：现有 19 个 @tool 工具不动，discover_tools() 自动扫；新插件用 @register_tool 带 category/deps —— 为什么双轨
  - `system.md` 是契约的硬规则（新增工具前必须同步工具清单 + 行为约束）—— 工具实施准则的元规则
- **改动后**：4 篇 ADR 落地，新人 onboarding 看 ADR 就能理解项目核心决策，不需要翻 git log。

---

## 一、背景与问题

1. **决策散落在 commit message**（"为什么选 X 不选 Y" 都在 git log 里）
2. **新人 onboarding 慢**：要理解 sync 工具为什么不用 asyncio 包装、checkpoint 为什么用 SQLite 而不是 Postgres，得翻 PR 讨论
3. **016 撤档教训**：P0 #1 "sync 工具阻塞事件循环" 是**误判**——LangChain 已经处理。这种误解如果没沉淀，下次还会再踩

---

## 二、目标

1. **写 4 篇 ADR**：
   - `0004-langchain-sync-tool-offload.md` — 016 撤档教训沉淀：为什么不需要 `@offload` 装饰器
   - `0005-checkpoint-sqlite-default.md` — LangGraph checkpoint 选 SQLite 而不是 Postgres 的理由
   - `0006-registry-tool-decorator-split.md` — `@tool` vs `@register_tool` 双轨的理由
   - `0007-system-md-as-contract.md` — `system.md` 是 Agent 行为契约的元规则
2. **使用 `docs/adr/` 现有编号顺序**：0004 / 0005 / 0006 / 0007
3. **格式遵循** ADR-0001 的轻量格式（标题 + 状态 + 决策 + 后果）

---

## 三、方案设计

每篇 ADR 50-150 行，结构：
- 标题 + 编号 + 状态
- 上下文：什么场景需要决策
- 决策：选了什么
- 后果：正反两面（好的 + 代价）
- 后续可考虑：什么条件会触发重新评估

### 3.1 ADR 0004 关键内容

> LangChain 1.x `BaseTool._arun` 默认调 `run_in_executor(None, self._run, ...)`，已把同步 @tool 函数 offload 到 asyncio 默认 ThreadPoolExecutor。
>
> 决策：**不引入 `@offload` 装饰器**（016 撤档）
>
> 后果：
> - ✓ 默认行为已经正确（实测 4 并发 × 2s sleep 总耗时 2.02s，事件循环未阻塞）
> - ✓ 不引入新模块、不污染工具装饰链
> - ✗ 当默认池被打满时（100+ 并发），需要换自定义池 —— 但目前用不到
>
> 重新评估条件：LangChain 升级若破坏默认 offload 行为；或实际并发 > 1000/s

### 3.2 ADR 0005 关键内容

> LangGraph checkpoint 选 SQLite（默认 base 安装） + Redis（dist 可选装）。
>
> 决策：
> - base: `langgraph-checkpoint-sqlite`（轻量、零运维、单文件）
> - dist: `langgraph-checkpoint-redis`（分布式 worker 共享状态）
>
> 后果：
> - ✓ SQLite demo 阶段零成本
> - ✓ Redis 升级路径已留（dist worker 已用）
> - ✗ SQLite 写并发低（~100 writes/s），多 worker 同一会话写会锁

### 3.3 ADR 0006 关键内容

> 工具注册走双轨：现有 @tool（langchain 原生） + @register_tool（crawagent 自定义）。
>
> 决策：
> - **现有工具不动**：保持 @tool 装饰，避免一次性改 19 个文件
> - **新插件用 @register_tool**：一次性声明 category/deps 元数据
> - **discover_tools() 统一扫描**：先收 @register_tool 的 _REGISTERED，再扫 tools/*.py 里的 BaseTool 实例
>
> 后果：
> - ✓ 渐进迁移，零破坏
> - ✓ 新插件有清晰元数据，prompt 生成更准

### 3.4 ADR 0007 关键内容

> `crawagent/prompts/system.md` 是 Agent 行为的**契约层**——所有工具调用规则、行为约束都在这里。
>
> 决策：
> - 新增 / 改工具**必须**同步 system.md（工具清单、硬规则、行为边界）
> - system.md 改动触发 PR review（不能随意改）
> - 不在 system.md 写过的约束 = 不约束（避免"心照不宣"的隐性规则）
>
> 后果：
> - ✓ Agent 行为可追溯、可验证
> - ✓ system.md 是新人了解项目的入口
> - ✗ 改动 system.md 的 review 成本（每次 PR 都要过 system.md diff）

---

## 四、涉及文件清单

| 操作 | 文件 |
|------|------|
| 新增 | `docs/adr/0004-langchain-sync-tool-offload.md` |
| 新增 | `docs/adr/0005-checkpoint-sqlite-default.md` |
| 新增 | `docs/adr/0006-registry-tool-decorator-split.md` |
| 新增 | `docs/adr/0007-system-md-as-contract.md` |
| 更新 | `docs/agents/domain.md`（如已存在 ADR 索引） |

---

## 五、验证方式

1. **4 篇 ADR 文件存在**：`ls docs/adr/000[4-7]*.md` 都命中
2. **CONTEXT.md 词汇表更新**：如有 "ADR" / "checkpoint" / "register_tool" 等新术语，加上
3. **全量 pytest 不回归**：基线 399 → ≥399 passed（纯文档，无代码改动）

---

## 六、后续可扩展（不在本次范围）

- ADR 模板化（添加 status: proposed / accepted / superseded / deprecated 字段）
- 季度 ADR review 会议（定期复审是否需重新评估）
- 把"016 撤档"全过程写入 wiki，作为"诚实降级"案例

---

## 七、实施顺序建议

1. 写 0004（最关键，016 教训沉淀）
2. 写 0005（架构选型）
3. 写 0006（registry 决策）
4. 写 0007（system.md 契约）
5. 跑 git log 检查是否有相关 commit 引用
6. 写 §八 实施记录

---

## 八、实施记录

**实施日期**：2026-09-20
**实施人**：Agent（指挥官 Joker 批准）
**关联提交**：本批次（018-022 + 批次 2 共 14 项）一次 commit

### 8.1 实际改动

| 序号 | 操作 | 文件 | 说明 |
|------|------|------|------|
| 1 | 新增 | `docs/adr/0004-langchain-sync-tool-offload.md` | 沉淀 016 教训 — LangChain 1.x `BaseTool._arun` 默认 `run_in_executor(None, ...)`，不引入 `@offload` 装饰器（实测 no-op，016 已撤档） |
| 2 | 新增 | `docs/adr/0005-checkpoint-sqlite-default-redis-optional.md` | LangGraph checkpoint 选型 — base 走 SQLite（轻量零运维），dist 多 worker 走 Redis（共享会话），不用 Postgres/MySQL |
| 3 | 新增 | `docs/adr/0006-registry-tool-decorator-dual-track.md` | 工具注册双轨决策 — 现有 19 个 `@tool` 不动 + 新插件 `@register_tool(category=, deps=)`，`discover_tools()` 统一扫描 |
| 4 | 新增 | `docs/adr/0007-system-md-as-agent-contract.md` | `crawagent/prompts/system.md` 是 Agent 行为契约层 — 新增/改工具必须同步，约束在 system.md 集中可见 |

### 8.2 ADR 风格遵循

现有 ADR 风格（参照 0001-0003）：
- 标题 `# 编号 — 一句话决策`
- 第一段：背景 + 决策（短，1 段）
- `## Considered Options`（备选 + 选定 + 否决理由）
- `## Consequences`（正反两面 + 重新评估条件）

4 篇新 ADR 全部遵循此格式。每篇 50-100 行，重点突出决策理由 + 重新评估条件，不写流水账。

### 8.3 验证结果

- **`ls docs/adr/`**：7 篇 ADR 全部存在（0001-0007）✓
- **`uv run pytest tests/ -q`**：410 passed, 6 warnings in 76.82s（纯文档，基线不变）✓
- **`docs/agents/domain.md`**：无需更新（domain.md 写"read ADRs that touch the area"自动涵盖 0001-0007，无需手工索引）

### 8.4 4 篇 ADR 关键点摘要

| ADR | 一句话 | 关键决策 | 重新评估条件 |
|-----|--------|----------|-------------|
| 0004 | LangChain 1.x 默认 offload | 不引入 `@offload` 装饰器 | LangChain 升级破坏默认行为 / 并发 > 1000/s |
| 0005 | Checkpoint SQLite/Redis 双层 | 单 worker SQLite / 多 worker Redis；不用 Postgres | 单机出现写瓶颈（> 50 writes/s） |
| 0006 | 注册双轨 `@tool` + `@register_tool` | 现有 19 个不动 + 新插件 `@register_tool` | 行为不可调和差异 / `_HARD_CODED_META` 膨胀 > 30 条 |
| 0007 | `system.md` 是 Agent 契约 | 新增/改工具必同步 system.md | 自动生成工具清单部分（保留行为约束段） |

### 8.5 实施经验

1. **ADR 沉淀 ≠ 流水账**：现有 0001-0003 都极简（每篇 10-20 行），关键信息密度高。4 篇新 ADR 严格遵循"决策 + 备选 + 后果 + 重新评估条件"四段式，避免落入"详细介绍背景"的流水账陷阱。
2. **016 撤档教训最佳沉淀时机**：实施 ADR 时刚好是 016 撤档 + 021 docstring 之后，020 HTTP 合并又遇到类似"延迟 import 避循环"问题——印证 0006 双轨 ADR 提到的"helper 模块不依赖工具"原则在 spec 之外仍频繁踩坑，沉淀正确。
3. **auto-index 的胜利**：`docs/agents/domain.md` 自动覆盖"read ADRs that touch the area"，无需手工维护 ADR 索引表——这种"按需读"模式比"集中索引"更抗 stale。
4. **020 留下的"helper 不依赖工具"教训 → 0006 ADR**：020 实施时遇到的"crawl_tool 延迟 import `_http`"循环 import 模式蔓延风险，刚好印证 0006 ADR 提到的双轨原则。这是 ADR 体系自我维护的范例：实施遇到的问题 → ADR 沉淀 → 下次新作者读 ADR 避坑。

### 8.6 后续

本批 018-022 五项规格全部完成。继续推进批次 2（9 项：跨 major 依赖升级 + long files 拆分 + 前端测试 + bilibili 6 端点合并 + 长函数拆）。本批 14 项整体 commit（per 指挥官确认）。