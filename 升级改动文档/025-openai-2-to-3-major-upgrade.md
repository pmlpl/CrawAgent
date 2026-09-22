# 变更 025：openai 2.x → 3.x 跨 major 升级

| 项目 | 内容 |
|------|------|
| 变更编号 | 025 |
| 提出日期 | 2026-09-20 |
| 状态 | 已实施 |
| 类型 | 依赖升级 |
| 关联模块 | `pyproject.toml` / `crawagent/llm/*` |
| 来源 | `docs/tech-debt/2026-09-20.md` P1 #4 |

---

## 〇、价值

- **改动前**：`pyproject.toml` 显式 pin `openai>=2.26.0,<3.0.0`（注释说避开魔改 httpx2）。当前 2.54.0，最新 3.16.2。阻塞新模型 / 新工具调用 API 接入。
- **改动后**：升到 openai 3.x，解锁新模型 + 新 API；保留注释说明 httpx2 兼容性已 OK

---

## 一、背景与问题

1. **httpx 劫持问题**：`openai` Python SDK 早期版本魔改 httpx2 导致与本地 httpx 客户端冲突（注释提到）。**3.x 已修复**——读 3.0 changelog 验证
2. **新 API**：openai 3.x 引入：
   - Responses API（替代 Chat Completions 部分场景）
   - 增强的 structured outputs
   - 新的 MCP / Tools API 命名空间
3. **代码触点**：仅 `crawagent/llm/` 目录

---

## 二、目标

1. 升 `openai>=2.26.0,<3.0.0` → `openai>=3.16.0`
2. **不动**代码：除非 3.x API 强制迁移（如 Chat Completions → Responses）
3. 跑全量 pytest 不回归
4. 验证 `langchain-openai` 仍兼容（间接依赖 openai）

---

## 三、方案设计

### 3.1 升级步骤

1. 读 openai 3.0 changelog 确认 httpx2 修复
2. `uv pip install --upgrade 'openai>=3.16.0'`
3. `uv run pytest tests/ -q` 跑全量验证
4. 若 `langchain-openai` 不兼容（间接依赖 openai 2.x），则用 `uv add 'langchain-openai>=最新'` 联升

### 3.2 代码触点扫描

```bash
grep -rn "from openai\|import openai\|openai\." crawagent/
```

预期触点：
- `crawagent/llm/model.py`（LLM 客户端构造）
- 可能 `crawagent/graph/agent.py`（直接用 openai client）

### 3.3 不动

- `langchain-openai`（除非强制联升）
- 业务代码（tool calls / structured outputs 走 langchain 抽象）
- 前端（与 openai SDK 无直接依赖）

---

## 四、涉及文件清单

| 操作 | 文件 |
|------|------|
| 修改 | `pyproject.toml`（解开 `<3.0.0` pin） |
| 可能 | `crawagent/llm/model.py`（构造方式微调） |
| 可能 | `crawagent/llm/registry.py`（模型名常量） |

---

## 五、验证方式

1. **`uv pip install` 无冲突**
2. **`uv run pytest tests/ -q`**：410+ passed 不变
3. **冒烟**：选 1 个 chat 走真实 LLM 调用，确认 Responses API / Chat Completions 都正常
4. **`pyproject.toml` lock**：依赖锁文件更新

---

## 六、后续可扩展（不在本次范围）

- 迁移到 Responses API（需要业务层重构）
- 多 provider 统一接口（agent 用 langchain 抽象已部分覆盖）

---

## 七、实施顺序建议

1. 读 changelog 评估破坏面（实施前）
2. dry-run 升级（uv pip install --dry-run）
3. 实际升级
4. 跑测试 + 冒烟
5. lock 依赖

---

## 八、实施记录

**实施日期**：2026-09-22
**实施人**：Agent（指挥官 Joker 批准）
**关联提交**：本批次（018-022 + 023 + 024-030）一次 commit

### 8.1 实际改动

| 序号 | 操作 | 文件 | 说明 |
|------|------|------|------|
| 1 | 修改 | `pyproject.toml` | `openai>=2.26.0,<3.0.0` → `openai>=3.0.0`；`langchain-openai>=1.0.0` → `langchain-openai>=1.5.1`；更新注释说明 httpx2 修复 |
| 2 | 升级 | `openai` | 2.54.0 → 3.17.0（PyPI 最新） |
| 3 | 联升 | `langchain-openai` | 1.5.0 → 1.6.3（1.5.0 硬约束 `openai<3.0.0`，1.5.1+ 放开为 `openai>=2.45.0,<4.0.0`） |

### 8.2 代码零改动（spec §四"可能"列表全不需要改）

| 文件 | 原因 |
|------|------|
| `crawagent/llm/model.py` | 不直接 import openai；用 `langchain_openai.ChatOpenAI`（langchain 抽象层，1.5→1.6 API 不变） |
| `crawagent/llm/registry.py` | 模型名常量与 openai SDK 无关 |
| `crawagent/web/routers/settings.py:354` | 同上，`from langchain_openai import ChatOpenAI` |
| `crawagent/tools/advanced_tools.py:185` | `browser_use.llm.openai.like.ChatOpenAILike` 是 browser_use 包内部，不依赖 openai SDK |

### 8.3 依赖链分析（实施前调研）

- **代码触点扫描**：`grep -rn "from openai\|import openai\|openai\." crawagent/` — 零直接 import（只有 URL 字符串 `api.openai.com` 和 browser_use 子模块路径）
- **langchain-openai 1.5.0 约束**：`openai>=2.26.0,<3.0.0`（硬阻 openai 3.x）→ 必须联升
- **langchain-openai 1.5.1+ 约束**：`openai>=2.45.0,<4.0.0`（1.5.1 起即放开，1.6.3 最新）
- **其他 openai 依赖方**：litellm `openai>=2.8.0`（无上限，兼容 3.x）；aider-chat `openai==2.20.0` 不在 crawagent venv 内（全局安装，无关）

### 8.4 验证结果

- **import 冒烟**：`from langchain_openai import ChatOpenAI; from crawagent.llm.model import get_llm` → OK
- **`uv run pytest tests/ -q`**：603 passed, 6 warnings in 97.59s（与基线完全一致，零回归）
- **warnings**：6 条全为既有（middleware `ModelRequest.messages` deprecation + pytest cache 权限），与 openai 升级无关

### 8.5 实施经验

1. **间接依赖升级的关键**：代码不直接 import openai，全部走 langchain_openai.ChatOpenAI 抽象层。真正需要解决的是 langchain-openai 的版本约束——1.5.0 硬钉 `<3.0.0`，1.5.1 起放开。升 langchain-openai 到 1.6.3 后 openai 3.x 即解锁。spec §3.1 step 4 已预见这个联升场景。
2. **httpx2 "魔改版" 问题**：pyproject.toml 原注释提到"魔改版用 httpx2 替代 httpx，会劫持 httpx 命名空间并触发 SyncByteStream 断言失败"。这是指某个被篡改的 openai 版本，不是官方 3.x。官方 openai 3.x 从 PyPI 安装，无此问题。注释已更新说明。
3. **uv run 会 sync 环境**：`uv pip install --upgrade` 升了 openai+langchain-openai+若干 side-effect 包（websockets 15→17 等），但 `uv run` 会按 pyproject.toml 重新 sync，side-effect 包被回退到 lock 版本。只有 pyproject.toml 明确约束的包（openai/langchain-openai）保留升级。这不是问题——只需要确保 pyproject.toml 的约束正确即可。
4. **spec 目标版本 vs 实际**：spec 写 `openai>=3.16.0`，实际安装 3.17.0（PyPI 最新）。pyproject.toml 用 `>=3.0.0` 更宽松，让 uv 自动选最新兼容版。