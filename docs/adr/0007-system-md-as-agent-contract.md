# 0007 — `crawagent/prompts/system.md` 是 Agent 行为契约层，新增/改工具必须同步

`crawagent/prompts/system.md` 不是普通文档——它是 Agent 行为的**契约层**：工具清单、硬规则（`BATCH-FIRST` / `ASK-USER` / 脚本阶梯等）、行为边界都在这里。改/加工具**必须**同步 system.md，否则 Agent 行为与文档脱节（典型症状：prompt 让 Agent 用某工具但工具实际不存在，或反之）。system.md 改动触发 PR review（不能随意改）。

## Considered Options

- **system.md 自动从代码生成**（如 introspect `@tool` 函数列表）：避免脱节，但丢失"行为约束"那部分（脚本阶梯、批量先于单条等无源码对应）。契约=约束+事实，缺一不可。
- **system.md 是软文档、随意改**：已尝试，长期导致 Agent 行为不可预测——新作者照 prompt 做事但代码不响应。
- **契约层（当前方案）**：system.md 是单一可信源（single source of truth）；工具代码可由 system.md 反推意图，反之亦然。

## Consequences

- Agent 行为可追溯、可验证：所有硬规则在 system.md 集中可见，新人 onboarding 看 system.md 就能知道 Agent 该怎么走
- system.md 改动 review 成本：每次 PR 涉及工具/规则都要过 system.md diff（PR review 清单加一条"system.md 是否需要同步"）
- 不在 system.md 写过的约束 = 不约束（避免"心照不宣"的隐性规则）：新约束提 PR 时必须补 system.md
- tool 数量 / category 变化必须同步：spec §三 写"改工具必同步 system.md 的工具清单 + 条数与相关硬规则"；CI 守门：`scripts/_audit_docstrings.py` 类似的位置可加 `scripts/_audit_tool_count.py` 验证 system.md 工具数 == `discover_tools()` 数
- 重新评估条件：自动生成 system.md 工具清单部分（保留行为约束段）；或新增"动态约束"维度（如按用户偏好定制 system.md 子段）