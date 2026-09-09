# 0003 — 聊天即加 MCP 不加 command 白名单，靠 ask_user 可见性闸门

变更 007 让用户与 AI 聊天即可增删停用 MCP server。stdio 型 MCP server 的 `command` 字段是任意可执行命令——AI 从用户给的仓库提炼配置后，直接 `ask_user` 请求确认。决定：**不加 command 路径白名单、不做命令审计**，闸门是 `ask_user` 题面里展示完整 server 配置（含 command），用户看到具体要执行什么再决定。

## Considered Options

- **command 白名单**（绝对路径 / 项目根 / 已知目录内）：本地单机部署下过度防御，且会让"把仓库交给 agent 自动配"这个核心诉求处处碰壁——用户给的 MCP server 命令路径千差万别，白名单会频繁误杀。
- **http url 公网告警**：MCP server 本就该能指向公网（远端托管的服务），告警意义不大。
- **完全无闸门、AI 直接加**：违反 ASK-USER 硬规则（改系统配置属需授权），且 stdio command 是可执行代码，不能盲批。
- **选定：不加白名单 + ask_user 题面展示完整配置**：可见性优于白名单——用户在确认时看到具体 command，比一条会被绕过/误杀的路径规则更实用。与现有 `run_custom_script`（跑任意代码、本地用户信任自己的 agent）同 posture；MCP add 多了 ask_user 确认，比 run_custom_script 更严。

## Consequences

- AI 提炼出的 command 若恶意/错误，唯一防线是用户在 ask_user 里看到并拒绝——system.md 硬规则要求题面必须展示完整配置（含 command），不可盲确认。
- 本地单机部署假设成立（[[deployment-local-only-no-auth]]）：用户跑在自己的机器上，主动把仓库交给 agent，信任链是"用户→agent"。若未来做多租户/远端部署，此决策需重估（届时必须加白名单 + 审计）。
- `args` 空格分隔、`headers` JSON 字符串入参——避免 LLM 在嵌套结构上出错，沿用项目里 `run_custom_script` / `save_record` 已验证的"字符串入参、内部解析"模式。
- 生效延迟一轮（reset_agent_cache 下轮装入）：LLM 工具列表轮次开始即 bind 死，本轮热加载不可行；AI 加完须告知"下条消息生效"，不能谎称当轮可用。
