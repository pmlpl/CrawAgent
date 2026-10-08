# 0009 — 会话存储文件主盘化：文件夹管每轮内容，数据库管运行时状态 + 索引

修正 ADR-0008。ADR-0008 当时定「混合存储，文件夹管活会话、数据库管死会话精华」，但 039 实现把活会话的热数据（messages）也落进了数据库（LangGraph checkpointer state），会话文件夹沦为惰性归档副本（仅 save_record 触发时才写）。sessions.db 膨胀至 1.8GB 且随使用继续涨，用户在资源管理器里看不见会话内容、备份单会话 = 备份整个 DB、删 `data/sessions/<sid>/` 只删归档副本不删 DB 里桥留的 state。

本 ADR 把方向修正到 ADR-0008 当年的本意：**文件夹管活会话的每轮内容，数据库管运行时状态 + 筛选的档案/元信息索引**。

## Considered Options

- **全数据库**（现状延续，messages 留 checkpointer）：检索/断点续跑强，但用户对会话数据无掌控感、DB 膨胀不可控、单会话备份/迁移困难、删目录 ≠ 清会话。
- **全文件**（连 checkpointer state 也挪文件）：人可读最彻底，但 LangGraph 断点续跑机制（中断恢复、工具调用中间态）依赖 sqlite checkpointer，全挪要重写运行时状态后端，丢断点续跑能力。
- **选定：文件主盘 + DB 精简索引**。会话内容主盘是 `data/sessions/<sid>/conversation.md`（每轮追加，人可读、可备份、删目录即清会话）；数据库退为「运行时状态索引 + 筛选的结构化索引」——checkpointer 保留 sqlite 但定期清理老 state（留最近 N 轮用于断点续跑，老内容已在 conversation.md 兜底）；FTS5 知识库 + meta_store 留 DB（它们本就是「筛选的档案/元信息」索引，留 DB 合理）。EventLog 是内存 list，与持久化无关，不动。

## Consequences

- 会话创建即建 `data/sessions/<sid>/`（含 `conversation.md` 头 + `meta.json` + `archive/`），不再惰性。
- 每轮完成把该轮 messages 追加到 `conversation.md`（O(1) 追加，失败不阻断主流程）。
- checkpointer 定期清理：保留每会话最近 N 轮 state（N 用真实数据校准，初值 10），老 state 删除前先确认该轮已在 conversation.md（兜底校验防丢）；清理走 ask_user 授权（沿用 ADR-0008「不自动清理」纪律）。
- 档案双写方向对调：盘为主、库为索引（save_record 的 FTS5 检索索引留 DB，md 快照留 archive/ 已有，方向一致）。
- ADR-0008 的「库为主、盘为副本」表述被推翻；「文件夹管活会话」本意被落实（从口号变成每轮落盘的实际主盘）。
- EventLog（内存 list）、FTS5 知识库、meta_store 不动。
- 一次性迁移：老 checkpointer state 的 messages 导出为各会话 conversation.md，清老 state，DB 瘦身。
- 后续可演进：若定期清理稳定后想彻底脱离 DB state，可再立 ADR 评估改 MemorySaver（本次不做，丢断点续跑能力代价大）。
