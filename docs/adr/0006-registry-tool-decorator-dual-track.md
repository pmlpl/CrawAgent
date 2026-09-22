# 0006 — 工具注册双轨：现有 `@tool` 不动 + 新插件 `@register_tool(category=, deps=)`

工具注册走双轨：现有 19 个 `@tool`（langchain 原生）+ `@register_tool`（crawagent 自定义封装）。`discover_tools()` 先收 `@register_tool` 的 `_REGISTERED`，再扫 `tools/*.py` 里的 `BaseTool` 实例，统一返回 `ToolSpec` 列表（带 category/deps 元数据）。现有工具**不动**，新插件**优先用 `@register_tool`**。

## Considered Options

- **统一改所有工具为 `@register_tool`**（一次性迁移）：元数据完整，但要改 19 个文件 + 验证每个工具的 langchain 行为兼容（`@tool` 在 `BaseTool` 路径上有细微差异）。风险大、无明显收益。
- **只保留 `@tool`，元数据硬编码**（registry.py 现存的 `_HARD_CODED_META` 字典）：新增工具要手动登记 category/dictionary，错写死率高。
- **双轨（当前方案）**：现有工具不破坏，新插件有清晰元数据，零迁移成本。

## Consequences

- 渐进迁移，零破坏：现有 19 个工具一行不用改，`_HARD_CODED_META` 字典补元数据
- 新插件有清晰元数据：`@register_tool(category="site", deps=["requests"])` 一次性声明，prompt 生成更准（system.md 工具清单能按 category 分组）
- `discover_tools()` 单一扫描入口：先 `_REGISTERED` 后 `BaseTool` 实例，元数据从两路合并去重
- `@register_tool` 内部仍调用 `_lc_tool`（langchain `@tool`），所以 LangGraph/LangSmith 集成不变
- 元数据字段固定：`category ∈ {crawl, extract, save, site, script, mcp, skill, general}` + `dependencies: list[str]`，新增分类需同步 system.md 工具清单
- 重新评估条件：`@tool` 与 `@register_tool` 行为出现不可调和差异；或 `_HARD_CODED_META` 字典膨胀到难以维护（> 30 条），强制全量迁移