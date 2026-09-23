# 变更 026：mcp 1.x → 2.x 跨 major 升级

| 项目 | 内容 |
|------|------|
| 变更编号 | 026 |
| 提出日期 | 2026-09-20 |
| 状态 | 已阻塞（等 upstream l-m-a 适配 mcp 2.x） |
| 类型 | 依赖升级 |
| 关联模块 | `crawagent/tools/mcp_capture_tool.py` / `mcp_admin_tool.py` |
| 来源 | `docs/tech-debt/2026-09-20.md` P2 #5 |

---

## 〇、价值

- **改动前**：`mcp` 1.30.0，最新 2.2.0。MCP 协议本身在迭代；落后一个 major 阻塞新 server 接入
- **改动后**：升 2.x，follow MCP 协议新版本

---

## 一、背景与问题

1. **API 变化**：mcp 1.x → 2.x 改动了 server connection / tool listing API。**需读 changelog 评估**
2. **代码触点**：
   - `crawagent/tools/mcp_capture_tool.py`（wait_capture_ready / auto_sync_mcp_token / check_mcp_status / build_mcp_tools）
   - `crawagent/tools/mcp_admin_tool.py`（add_mcp_server）
   - `crawagent/graph/skills.py`（ensure_mcp_started / mcp_servers_status / get_mcp_tools）
3. **测试**：现有 mcp_admin / mcp_connection_error / mcp_fail_cache / mcp_capture_tool 等测试可能需要适配

---

## 二、目标

1. 升 `mcp>=1.30.0` → `mcp>=2.2.0`
2. **最小破坏**：仅调整必要的 API 调用，不重写业务逻辑
3. 跑全量 pytest 不回归
4. **冒烟**：手动测 1 个 stdio + 1 个 http MCP server 连接

---

## 三、方案设计

### 3.1 升级步骤

1. 读 mcp 2.0 changelog / migration guide
2. `uv pip install --upgrade 'mcp>=2.2.0'`
3. **逐模块测试：
   - mcp_capture_tool（最长）
   - mcp_admin_tool（add_mcp_server）
   - graph/skills.py（ensure_mcp_started 等）
4. 修 API 适配（具体改动看 changelog）

### 3.2 风险

- `StdioServerParameters` / `ClientSession` 构造可能改了
- `list_tools()` 异步路径可能改
- 错误类型 / 异常结构可能改

### 3.3 不动

- ADR-0003（不加 command 白名单）决策
- 业务层"通过 ask_user 确认 command"流程
- MCP server 配置 schema（不引入新字段）

---

## 四、涉及文件清单

| 操作 | 文件 |
|------|------|
| 修改 | `pyproject.toml` |
| 修改 | `crawagent/tools/mcp_capture_tool.py` |
| 修改 | `crawagent/tools/mcp_admin_tool.py` |
| 修改 | `crawagent/graph/skills.py` |
| 可能 | `tests/test_mcp_*.py` 系列 |

---

## 五、验证方式

1. **`uv run pytest tests/ -q`**：410+ passed 不变
2. **手动冒烟**：
   - 添加 1 个 stdio MCP server（如 `@modelcontextprotocol/server-filesystem`）
   - 添加 1 个 http MCP server
   - 连接 → 工具列表 → 调用工具 → 关闭
3. **error path**：连接失败 / 超时 / 命令不存在

---

## 七、实施顺序建议

1. 读 changelog
2. 升级 + 跑测试
3. 修失败测试 / 适配 API
4. 手动冒烟
5. lock 依赖

---

## 八、实施记录

**实施日期**：2026-09-22（初版）+ 2026-09-23（完整迁移分析）
**实施人**：Agent（指挥官 Joker 批准）
**状态**：**已阻塞——等 upstream l-m-a 适配 mcp 2.x（方案 A：等）**
**关联提交**：026 不入库（mcp 1.30.0 + langchain-mcp-adapters 0.3.2 保持现状）

### 8.1 决策：方案 A（等 upstream）

指挥官 2026-09-23 批准方案 A。理由：
1. mcp 1.30.0 协议层兼容 mcp 2.x server（JSON-RPC 线格式未变）
2. 当前功能不受影响，无被阻塞的业务需求
3. langchain-mcp-adapters 是活跃项目（0.3.2 发布于 2026-08-06），适配 mcp 2.x 是时间问题

### 8.2 mcp 2.x 完整迁移分析（2026-09-23 补充）

来源：mcp 官方迁移指南 https://py.sdk.modelcontextprotocol.io/v2/migration/

mcp 2.x 是 **4 层面全面 API 重构**，不是简单改名。shim 无法修复：

| 层面 | mcp 1.x | mcp 2.x | l-m-a 受影响文件 |
|------|---------|---------|-----------------|
| **模块结构** | `mcp.server.fastmcp` / `mcp.shared.session` / `mcp.shared.context.RequestContext` | `mcp.server.mcpserver`（旧路径是 stub 故意报错）/ `mcp.shared.session` 删除 / `RequestContext` 删除 | callbacks.py / tools.py |
| **字段名** | camelCase（`inputSchema`, `nextCursor`） | snake_case（`input_schema`, `next_cursor`） | 所有 type 访问 |
| **ClientSession** | `get_server_capabilities()` / `cursor` 参数 / timedelta 超时 | 全删 / 超时改 float 秒 / BaseSession 删除 | client.py / sessions.py |
| **传输层** | httpx + httpx-sse | httpx2（传错类型**静默失败**）+ opentelemetry-api 硬依赖 | sessions.py |

### 8.3 Shim 尝试记录

尝试了 2 层 shim：
1. `mcp.shared.context.RequestContext = BaseContext` → 解决 callbacks.py:8
2. `mcp.shared.session` 模块 re-export `mcp.client.session.ProgressFnT` → 解决 callbacks.py:9

第 3 层断裂：`mcp.server.fastmcp` 在 mcp 2.x 是故意报错的 stub（`raise ModuleNotFoundError("FastMCP was renamed to MCPServer")`）。tools.py import `FastMCPTool` 和 `ArgModelBase, FuncMetadata` 从 `mcp.server.fastmcp.*`，这些内部 API 在 mcp 2.x 完全重构。

结论：每层 shim 暴露下一层断裂，shim 不可行。

### 8.4 替代方案（未来重试时可选）

| 方案 | 工作量 | 适用场景 |
|------|--------|----------|
| **A: 等 upstream**（已选） | 零 | 当前无紧急需求；定期检查 l-m-a 新版本 |
| **B: 直接用 mcp 2.x SDK** | ~200 行 | 代码只在 `skills.py:545` 用 `MultiServerMCPClient`；替换成 `mcp.ClientSession` + 手动转 LangChain Tool |
| **C: Fork l-m-a** | ~400 行 | 保持 l-m-a 抽象层；按迁移指南更新全部 import + 字段名 + API 调用 |

### 8.5 重试检查清单

当 langchain-mcp-adapters 发布新版本时，执行以下检查：

```bash
# 1. 查新版本约束
python -c "import importlib.metadata; print(importlib.metadata.requires('langchain-mcp-adapters'))"
# 如果 mcp 约束放开到 >=2.0.0 或 <3.0.0 → 可以重试

# 2. 升级 + 测试
uv pip install --upgrade langchain-mcp-adapters mcp
uv run python -c "from langchain_mcp_adapters.client import MultiServerMCPClient; print('OK')"
uv run pytest tests/ -q

# 3. 如有 API 变更，修 crawagent/graph/skills.py:545（唯一 import 点）
```

#### 8.1.1 代码触点扫描

`grep -rn "from mcp\b\|from mcp\.\|import mcp\b" crawagent/` — **零直接 import**。代码库通过 `langchain_mcp_adapters.client.MultiServerMCPClient` 间接使用 mcp SDK（仅 `crawagent/graph/skills.py:545` 一处）。mcp 是 langchain-mcp-adapters 的 transitive dependency，不在 pyproject.toml 中声明。

#### 8.1.2 依赖链分析

| 包 | 当前 | 约束 |
|----|------|------|
| `mcp` | 1.30.0 | —（transitive） |
| `langchain-mcp-adapters` | 0.3.2 | `mcp>=1.24.0,<2.0.0`（0.3.2 重新钉回） |
| `langchain-mcp-adapters` 0.3.1 | — | `mcp>=1.24.0`（无上限，允许 2.x） |
| `langchain-mcp-adapters` 0.3.0 | — | `mcp>=1.9.2`（无上限） |

关键发现：0.3.1 放开了 mcp 上限，但 0.3.2（2026-08-06 发布）重新钉回 `<2.0.0`——说明 0.3.2 发现 mcp 2.x 有破坏性变更才回退。

#### 8.1.3 httpx2 共存验证

mcp 2.2.0 依赖 `httpx2>=2.5.0`（PyPI 独立包），openai 3.17.0 依赖 `httpx>=0.23.0,<1`。下载 httpx2 wheel 检查模块结构：

- httpx2 wheel 顶层包：`httpx2`（**不是** `httpx`）
- httpx2 **不劫持 httpx 命名空间**——两个包可共存

原 pyproject.toml 注释提到的"魔改版用 httpx2 替代 httpx"是指被篡改的 openai 版本，不是标准 mcp 2.x。

#### 8.1.4 实际升级测试

安装 `mcp 2.2.0 + langchain-mcp-adapters 0.3.1` 后验证 import：

```python
from langchain_mcp_adapters.client import MultiServerMCPClient
# ImportError: cannot import name 'RequestContext' from 'mcp.shared.context'
```

**mcp 2.x 移除了 `mcp.shared.context.RequestContext`**，改为 `BaseContext` / `TransportContext` / `DispatchContext` 等新结构。langchain-mcp-adapters 0.3.1 的 `callbacks.py:8` 仍 import 旧名 `RequestContext`，导致 ImportError。

这就是 0.3.2 重新钉 `mcp<2.0.0` 的根本原因——不是 httpx2 冲突，而是 mcp 2.x API 重构。

### 8.2 回退

```bash
uv pip install 'langchain-mcp-adapters==0.3.2' 'mcp<2.0.0'
# langchain-mcp-adapters 0.3.1 → 0.3.2
# mcp 2.2.0 → 1.30.0
```

回退后 `uv run pytest tests/ -q`：603 passed, 6 warnings（与基线一致）。

### 8.3 解阻条件

026 需等待 langchain-mcp-adapters 发版适配 mcp 2.x 的 `BaseContext`/`TransportContext` 新 API。当前最新 0.3.2（2026-08-06）未适配，PyPI 无 pre-release。

**建议的跟进方式**：定期检查 langchain-mcp-adapters 新版本，当其 `requires_dist` 中 mcp 约束放开到 `>=2.0.0` 或 `<3.0.0` 时即可重试 026。升级时只需：
1. `uv pip install --upgrade langchain-mcp-adapters mcp`
2. `uv run pytest tests/ -q`
3. 如有 API 变更，修 `crawagent/graph/skills.py:545`（唯一 import 点）

### 8.4 实施经验

1. **transitive dependency 升级的 upstream blocker**：mcp 不在 pyproject.toml 中，是 langchain-mcp-adapters 的间接依赖。升 mcp 2.x 被 langchain-mcp-adapters 的 API 适配进度阻塞。与 025 openai 不同（langchain-openai 1.5.1+ 已放开 openai 3.x），langchain-mcp-adapters 没有任何版本适配 mcp 2.x。
2. **"重新钉回"是强信号**：0.3.1 无上限 → 0.3.2 钉回 `<2.0.0`，版本约束收紧意味着 upstream 踩到了真实破坏。看到这种模式应该优先验证而非强行升级。
3. **诚实降级原则**：026 不入库，spec 状态改为"已阻塞"，实施记录完整记录调研过程和解阻条件。不假装完成，不留半残状态。