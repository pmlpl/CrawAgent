# 变更 008：save_record 知识库检索层（FTS5 全文检索 + retrieve-first 闭环）

| 项目 | 内容 |
|------|------|
| 变更编号 | 008 |
| 提出日期 | 2026-09-10 |
| 状态 | 已实施（2026-09-10） |
| 类型 | 功能增强 |
| 来源 | CrawAgent 会话 `8910a6a48ac3` 自评 crawl4ai 后的结论：已有 save_record 写 SQLite 的雏形，缺检索层 + retrieve-first 闭环 |
| 关联模块 | crawagent/tools/save_tool.py、crawagent/tools/query_tool.py（或新增 search_tool.py）、crawagent/prompts/system.md、tests/ |

---

## 〇、价值

- **改动前**：`save_record` 把抓取内容（含正文 content）写进 SQLite `crawl_records`，但 `list_crawled_resources` 的 keyword 只 `LIKE` 查 title/url，**不查 content**。库里存了一堆正文，agent 却没法"按内容检索找到对的那一段"——每次还得现场发请求重抓。CrawAgent 自己点破："一万个文件躺在磁盘上，我并不会因此变强——缺的是检索层。"
- **改动后**：给 `crawl_records` 加 FTS5 全文索引（title + content），新增 `search_knowledge(query)` 工具做 MATCH 检索 + 返回命中文段 snippet；system.md 加 RETRIEVE-FIRST 硬规则——agent 抓站前先查本地库，命中直接用，miss 才出门抓，抓完顺手入库。SQLite 升级成"越用越厚的本地知识库"，零新依赖（FTS5 是 Python sqlite3 内置）。

## 一、现状（已查实）

`crawl_records` 表列：`id, url, title, content, extra_data, created_at, platform, save_path, session_id`。content 已存正文。
`list_crawled_resources(platform, keyword, limit, scope)` 的 keyword 走 `title LIKE ? OR url LIKE ?`，**不碰 content**。
`_init_db` 已有兼容迁移机制（ADD COLUMN 无损加字段），可复用同模式加 FTS 表 + 触发器。

## 二、目标

1. **FTS5 全文索引**：建虚拟表 `crawl_records_fts` 镜像 `crawl_records` 的 `title + content`（外加 url/platform/session_id 作为 unindexed 列方便回查），用 INSERT/UPDATE/DELETE 触发器与主表同步。迁移幂等 + 回填存量行。
2. **`search_knowledge(query, limit=10, scope="session")` 工具**：FTS5 MATCH 检索 title+content，按 bm25 排序，返回 `snippet()` 高亮文段 + url/title/id。scope 规则同 list_crawled_resources（默认当前会话，"所有历史"才 scope=all）。
3. **保留 `list_crawled_resources`** 不动——它是元数据列表工具（"我抓过哪些"），`search_knowledge` 是内容检索工具（"库里有没有讲 X 的段"），职责分开。
4. **system.md 加 RETRIEVE-FIRST 硬规则**：抓任何 URL/主题前，先 `search_knowledge(关键词)`；命中且内容够用 → 直接用，不抓；miss 或不够 → 才调爬取工具，抓完 `save_record` 入库。让"库越用越厚、agent 越用越快"成立。
5. **不做**：向量/embedding 检索（进阶方案，需 embedding 模型，留 009）；跨库 join；crawl4ai 集成本身（它是摄入层，独立考虑）。

## 三、方案设计

### 3.1 FTS5 schema + 触发器（在 `_init_db` 里幂等建）

```sql
CREATE VIRTUAL TABLE IF NOT EXISTS crawl_records_fts USING fts5(
    title, content,
    content_rowid='id', content='crawl_records',   -- contentless-pull 模式，避免重复存正文
    tokenize='unicode61'   -- 默认分词，中文按字切（够用；jieba 分词留进阶）
);
```
实际用「external content」表（`content='crawl_records'`）：FTS 不重复存正文，只存索引，省空间；查询时 JOIN 回主表取 url/platform/session_id。

触发器（幂等：用 `CREATE TRIGGER IF NOT EXISTS`）：
- AFTER INSERT ON crawl_records → INSERT INTO fts(rowid, title, content) VALUES (new.id, new.title, new.content)
- AFTER UPDATE → 先 DELETE FROM fts WHERE rowid=old.id 再 INSERT 新值
- AFTER DELETE → DELETE FROM fts WHERE rowid=old.id

存量回填（迁移时一次性）：
```sql
INSERT INTO crawl_records_fts(rowid, title, content)
  SELECT id, title, content FROM crawl_records
  WHERE id NOT IN (SELECT rowid FROM crawl_records_fts);
```

### 3.2 `search_knowledge(query, limit=10, scope="session")`

```sql
SELECT r.id, r.url, r.title, r.platform, r.session_id,
       snippet(crawl_records_fts, 1, '[', ']', '...', 24) AS excerpt,
       bm25(crawl_records_fts) AS rank
FROM crawl_records_fts f
JOIN crawl_records r ON r.id = f.rowid
WHERE crawl_records_fts MATCH ?
  [AND r.session_id = ?]   -- scope=session 时
ORDER BY rank
LIMIT ?;
```
- query：FTS5 查询语法（空格分词 AND；用户自然语言直接传，工具内不做复杂改写；若需 OR 用引号包）。中文按字切够用。
- 返回：`Found N段 (scope=...):\n1. [平台] 标题 | url\n   文段摘录…\n2. ...`，miss 返回 `No match.` + 提示"可出门抓 + save_record"。
- scope 同 list_crawled_resources：默认 session，"所有历史"才 all。

### 3.3 system.md 契约

工具清单加 `search_knowledge`；新增硬规则段：
- **RETRIEVE-FIRST (HARD)**：用户要内容/答案时，先 `search_knowledge(主题关键词)` 查本地库。命中且内容够 → 直接基于库内容回答，**不爬取**。miss 或内容不足 → 才调 crawl/browse 抓，抓完成功必 `save_record` 入库。
- **入库时机**：一次成功提取（content 非空、非乱码）后 `save_record`；失败/空/乱码禁入库（沿用现有约束）。
- **范围**：search_knowledge 默认查当前会话；用户问"之前/历史上有没有"才 scope=all。

## 四、涉及文件清单

| 文件 | 改动 |
|------|------|
| crawagent/tools/save_tool.py | `_init_db` 加 FTS5 虚表 + 触发器 + 存量回填（幂等） |
| crawagent/tools/query_tool.py | 新增 `@tool search_knowledge(query, limit, scope)` |
| crawagent/tools/registry.py | `_TOOL_META` 加 search_knowledge（category=save） |
| crawagent/prompts/system.md | 工具清单 +1；新增 RETRIEVE-FIRST 硬规则段 |
| tests/test_knowledge_search.py | 新增：FTS 迁移幂等 / 存量回填 / 命中 snippet / scope 过滤 / miss 返回 |
| AGENTS.md / docs/00 / docs/04 | 工具数 +1（28），基线计数 |

## 五、验证计划

- `uv run pytest tests/ -q` 全绿（新增 test_knowledge_search）
- 离线冒烟：`save_record` 写 3 条 → `search_knowledge("FastAPI 路径参数")` 命中第 2 条 snippet → `search_knowledge("不存在的词")` miss
- 复刻 CrawAgent 会话里的演示：抓 FastAPI 3 页入库 → 查库回答（这次走 search_knowledge 而非 list_crawled_resources）

## 六、待批准后实施顺序

1. `_init_db` FTS5 迁移 + 触发器 + 回填（先跑迁移测幂等 + 存量）
2. `search_knowledge` 工具 + FTS5 MATCH/snippet/bm25
3. system.md 契约（清单 + RETRIEVE-FIRST）+ 工具数文档/基线
4. test_knowledge_search 回归测试
5. pytest + build + 提交

## 七、关联

- 依赖：无新依赖（FTS5 是 Python sqlite3 内置，需确认 .venv 的 sqlite 编译含 FTS5——绝大多数发行版默认含）
- 不做：向量检索（009）、crawl4ai 摄入层集成（独立考虑）
- 与 007/006/005 独立，无交叉
