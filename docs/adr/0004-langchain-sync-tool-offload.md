# 0004 — LangChain 1.x 默认已 offload 同步 @tool 到 asyncio 线程池，不引入 `@offload` 装饰器

变更 016 曾尝试引入 `@offload` 装饰器显式标记"这个同步工具应该 offload 到线程池跑"。实测发现 LangChain 1.x `BaseTool._arun` 默认实现就是 `return await asyncio.get_event_loop().run_in_executor(None, self._run, ...)`——同步工具调用在 asyncio 路径上**已经**自动 offload 到默认 ThreadPoolExecutor，不需要装饰器。装饰器是 no-op，反而污染工具装饰链。变更 016 已撤档（详见 `/upgrade-doc` skill「诚实降级」原则）。

## Considered Options

- **引入 `@offload` 装饰器**（变更 016 原方案）：标记意图 + CI 静态扫描可检。**已撤档**——实测 4 并发 × 2s sleep 总耗时 2.02s（与裸调无差），事件循环未阻塞。装饰器是 no-op，唯一作用是污染工具链。
- **裸用 `@tool`**（当前）：不引入新模块，不破坏现有 19 个工具的装饰器。LangChain 已处理 async 路径。
- **替换 `@tool` 为自定义 BaseTool 子类**：完全控制 `_arun` 行为。代价是破坏与 LangGraph/LangSmith 的集成（tool 自动纳管 / 描述渲染等），且 `@tool` 已在生态里被广泛测试。

## Consequences

- 默认行为已经正确：实测 4 并发 × 2s sleep 总耗时 2.02s（async gather 路径），事件循环不阻塞
- 不引入新模块、不污染工具装饰链
- 当默认 ThreadPoolExecutor 打满时（100+ 并发）需要换自定义池——但本项目目前没有这种场景，未来真出现再重评估
- `@offload` 装饰器不在仓库存在；新人 onboarding 看 system.md + tools/ 现有装饰器就能懂
- 重新评估条件：LangChain 升级若破坏默认 offload 行为；或实测并发 > 1000/s 且默认池耗尽