# 0005 — LangGraph checkpoint 选 SQLite（默认）+ Redis（可选），不用 Postgres/MySQL

LangGraph checkpoint 选型决定：base 安装默认 `langgraph-checkpoint-sqlite`（轻量、零运维、单文件），dist worker 可选装 `langgraph-checkpoint-redis`（多 worker 共享会话状态）。不用 Postgres / MySQL / 任何需要外部 DB server 的方案——单机部署占绝对多数，多 worker 分布式场景才用 Redis。

## Considered Options

- **SQLite + Redis 双层（当前方案）**：base demo / 单 worker 走 SQLite（`data/sessions.db` 单文件），dist 多 worker 走 Redis（共享会话状态）。两种部署模式都零运维启动。
- **Postgres（单一方案）**：支持高并发 + 备份恢复工具成熟，但需要外部 DB server，部署成本对单机 demo 不友好。`create_async_engine` + 异步驱动的复杂度也更高。
- **MySQL**：类似 Postgres，且 LangGraph checkpoint MySQL 实现成熟度不如 SQLite/Redis。
- **文件 JSON / pickle 自实现**：完全可控但要自己处理锁、原子写、崩溃恢复——重复造轮子，且跟 LangGraph 版本升级走形。

## Consequences

- SQLite demo 阶段零成本：单文件 `data/sessions.db` 备份/恢复 = `cp` 命令，开发者 onboarding 5 分钟跑通
- Redis 升级路径已留：dist worker 已用 `langgraph-checkpoint-redis`，多进程共享状态 OK
- SQLite 写并发低（~100 writes/s），多 worker 同一会话写会锁——因此 SQLite 只用于单 worker 场景
- 单 worker 模式下 SQLite checkpoint 写入仍走 WAL 模式（`checkpointer.py` build_checkpointer 时启用），崩溃恢复友好
- 重新评估条件：单机部署出现 checkpoint 写瓶颈（实测 > 50 writes/s）；或需要跨进程共享 checkpoint
- Postgres 升级路径：换 `langgraph-checkpoint-postgres` + `DATABASE_URL` 配置（langgraph 已支持），成本可控