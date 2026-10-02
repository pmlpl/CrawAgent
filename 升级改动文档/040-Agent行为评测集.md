# 变更 040：Agent 行为评测集

| 项目 | 内容 |
|------|------|
| 变更编号 | 040 |
| 提出日期 | 2026-10-02 |
| 状态 | 已完成（2026-10-02） |
| 类型 | 工程效率（Agent 行为回归保护） |
| 关联模块 | scripts/（新增评测脚本与黄金集）、tests/（新增契约测试）、crawagent/prompts/system.md（仅两行既有偏差校正） |

---

## 〇、价值

CrawAgent 的「听话程度」完全靠 system.md 调教，此前改提示词、换模型全凭手感，中间一层「LLM 拿着 system.md 会不会按规矩选工具」没有任何回归保护。

- **改动前**：往工具描述里多写一句 "Requires X installed"，Agent 就预判失败拒绝调工具（变更 013 实锤）；冒烟阶段手工测出过 2 个 prompt bug（脚本没固化）；system.md 里 "You have 21 powerful tools" 计数早已过期（实际清单 44 条 / registry 45 个工具）、fetch_rss_feed 漏登记在工具清单——这些漂移全靠人眼，没人发现。
- **改动后**：改 system.md 或换模型前后各跑一遍评测集，逐例 PASS/FAIL + 通过率报告直接指出哪条硬规则被改坏；工具清单与 registry 的漂移由 pytest 常驻契约测试当场拦截，不再依赖人眼。

本变更是 041（经验闭环）/042（MCP 热感知）的验收底座——041 的验收方式就是「040 评测集新增两类用例跑出增量分数」。

## 一、背景与问题

1. **历史实锤（为什么需要这把尺子）**：
   - 变更 013：@tool 描述写「Requires X installed」类依赖提示，Agent 学会了预判拒绝——不调工具、直接告诉用户装环境。靠人工发现、人工修提示词、人工回归。
   - 032/033 实施期间的手工冒烟测出 2 个 prompt bug（当时是临时场景脚本，跑完即弃，未固化成资产）。
2. **现有防线覆盖不到这一层**：tests/test_smoke.py 只验证「Agent 能构建」；各工具单测验证「工具本身行为正确」。唯独「LLM 面对任务会不会按 system.md 的硬规则选工具、按顺序换路、按契约先问再动」完全没覆盖——而这恰恰是换模型 / 改提示词时最容易坏的部分。
3. **既有过期实锤（2026-10-02 实测，可直接复现）**：
   - system.md 第 265 行 "You have 21 powerful tools"，实际 capabilities 清单 44 条、registry 工具 45 个；
   - `fetch_rss_feed` 在 registry 里但不在 system.md 工具清单里（registry 与清单差集实测仅此一个）。
4. **HANDOFF 方案的一处现实修正**：HANDOFF 写「可复用 scripts/agent_smoke.py 的 WS 协议基建」——该文件**从未入库**（git 全历史无记录，仅 HANDOFF 与记忆提及），无法复用。本规格改用**进程内直驱**（§3.2），不起服务、不走 WS，更贴合「测 LLM 决策」的目标。由此连带：原「随 040 顺手补 WS 层测试」不再搭车，该缺口维持挂账（见 §六）。

## 二、目标

1. **评测 harness**（`scripts/eval_behavior.py`）：直连配置的 LLM 跑真 Agent（真 system.md、真工具 schema、真中间件），工具执行替换为桩，逐例采集工具调用序列并按期望判定，输出逐例 PASS/FAIL + 通过率报告。
2. **黄金集 15 例**：覆盖 system.md 当前版（038/039 后）的主要硬规则与工具选择契约。
3. **常驻契约测试**（`tests/test_prompt_contract.py`）：无需 LLM 的静态一致性检查进 pytest，每次跑测试零成本执行。
4. **有效性自证**：往 system.md 注入一条坏规则 → 评测分数可测出下降 → 撤销后恢复（HANDOFF 指定的验收项）。

**不做什么（划界）**：
- 不做 LLM-as-judge（自由文本语义判定）——判定器全部确定性规则，保证分数稳定可复现、不引入「裁判本身的抖动」。
- 不做分数趋势库 / 看板——每次跑完出报告，前后对比人工看。
- 评测不进 pytest / CI——要真 LLM、要花钱，独立脚本按需运行；进 pytest 的只有 §3.5 的离线契约测试。
- 不修评测抓出的 Agent 行为问题——040 只造尺子；尺子量出的问题另行立项处理。
- 不补 WS 层测试——前提（复用 agent_smoke WS 基建）不成立，见 §一.4。

## 三、方案设计

### 3.1 总览：两把尺子

| | 常驻契约测试 | 行为评测集 |
|---|---|---|
| 位置 | tests/test_prompt_contract.py | scripts/eval_behavior.py + scripts/eval_cases.py |
| 防什么 | 文档与代码漂移（清单过期、规则误删、描述写坏） | LLM 不按 system.md 行事（换模型/改提示词的行为回归） |
| 何时跑 | 每次 pytest（离线、零成本） | 改 system.md 前后 / 换模型前后，手动按需（live、花 LLM 调用） |
| 判定 | 纯字符串/集合比对 | 工具调用序列 + 确定性匹配器 |

### 3.2 评测 harness（进程内直驱）

```
get_agent()                    # 真 LLM + 真 SYSTEM_PROMPT + 真工具 schema + 真中间件
  ↓ 工具执行入口全部换桩：保留 name/description/args_schema 供 LLM 绑定，
    func 与 coroutine 换成桩分发器（按用例剧本返回 canned 输出，同时记录 (工具名, 参数) 调用流水）
  ↓ agent.stream({"messages": [用户消息]})，逐步采集 AIMessage.tool_calls 与最终回复文本
  ↓ 判定器按该用例的期望逐条断言 → PASS / FAIL + 人话理由
```

设计要点：

- **桩剧本**：每例一份 dict，工具名 → 返回串列表（按调用次序逐个弹出，弹完循环最后一个），如 `{"crawl_webpage": [SPA壳HTML], "run_custom_script": [成功输出]}`。`ask_user` 桩按剧本队列作答（如 `["存入档案"]`），队列耗尽默认答「不用」。`save_record` / `save_to_file` 桩返回假成功不落盘——**评测全程不写真实 data/**（不依赖 conftest，评测是独立进程，无副作用靠桩保证）。
- **无 checkpointer**：get_agent() 传 None，不产生 checkpoint 文件；单轮独立对话，不污染真实会话。
- **中间件照常参与**：ScriptForcerMiddleware（3 连败强制 run_custom_script、调用上限）是系统的一部分，评测测的是「system.md + 中间件」的合力行为。
- **坏规则注入天然生效**：SYSTEM_PROMPT 在 agent.py import 时读盘，评测每次新起进程，注入后重跑即读新内容，无需任何热更处理。
- **顺序执行**：15 例 × 平均 3-6 个 LLM 决策步，预计几分钟；默认串行防限流。

CLI：

| 参数 | 作用 |
|------|------|
| `--filter 案例id` | 只跑指定案例（调试用） |
| `--model model_id` | 走 get_agent(model=...) 换模型对比 |
| `--out 路径.json` | 报告落 JSON（逐例明细），供改前/改后对比；缺省只打印 |
| `--retries N` | 失败例自动重跑 N 次，重跑通过标 FLAKY 不计通过（默认 0） |

报告形态（stdout 人读 + JSON 机读）：

```
01 retrieve_first_hit ............ PASS  序列: search_knowledge → (直接作答)
02 spa_shell ..................... FAIL  期望第 2 步 run_custom_script，实际调了 browse_and_crawl
...
通过率: 14/15 (93.3%)   模型: glm-5.2   system.md mtime: 2026-10-02 10:30
```

### 3.3 判定器词表（全部确定性）

| 匹配器 | 语义 |
|--------|------|
| expect_seq | 工具名有序子序列：顺序必须保持，允许中间插入额外调用（宽进） |
| first_tool | 第一个工具调用必须是 X（如 RETRIEVE-FIRST 的 search_knowledge） |
| forbid | 这些工具一次都不许出现 |
| count | 某工具调用次数断言（==1、==0、≤N），另支持总调用数上限 |
| arg_contains | 某工具某次调用的指定参数含某子串（如 ask_user 的 question 含「存入档案」） |
| reply_re | 最终回复文本匹配正则（如包含 KB 桩里的标记句、包含 Cookie 引导语） |

词表外允许每例附加一个小型自定义 check 函数（评测集本身是 Python 模块），仅用于词表表达不了的断言（如「回复中汉字不少于 20 个」的语言规则），优先用词表。

### 3.4 黄金集 15 例（以当前 system.md 为基准）

| # | id | 用户输入 | 桩剧本要点 | 期望 |
|---|----|----------|-----------|------|
| 01 | retrieve_first_hit | 帮我抓 https://blog.example.com/post/123 的正文 | search_knowledge 返回覆盖命中片段（内含标记句） | first_tool=search_knowledge；forbid crawl_webpage/browse_and_crawl；reply_re 命中标记句（从 KB 作答不重爬） |
| 02 | retrieve_first_miss | 同 01 | search_knowledge 未命中；crawl_webpage 返回正常 HTML | first_tool=search_knowledge；expect_seq 含 crawl_webpage；forbid browser_use_navigate |
| 03 | spa_shell | 抓 https://spa.example.com/app 的内容 | crawl_webpage 返回小于 5KB 的 SPA 壳；run_custom_script 成功 | expect_seq: crawl_webpage → run_custom_script；forbid browse_and_crawl（SPA 壳不许换浏览器工具） |
| 04 | three_failures | 抓 https://blocked.example.com/list | crawl_webpage ERR；browse_and_crawl ERR；run_custom_script 成功 | expect_seq: crawl_webpage → browse_and_crawl → run_custom_script；2 连败后不许再试第 3 个内置工具 |
| 05 | wallpaper_fail | 抓这个壁纸站的图 https://wallpaper.example.com/list | extract_wallpaper_list 返回 0 items | expect_seq: extract_wallpaper_list → run_custom_script；forbid crawl_webpage/browse_and_crawl |
| 06 | archive_ask_multipage | 把这个列表每一页都抓下来 https://news.example.com/list | search_knowledge 未命中；extract_list_paged 返回 15 items；ask_user 答「存入档案」 | expect_seq: extract_list_paged → ask_user → save_record；arg_contains ask_user question 含「存入档案」（不许不问就存） |
| 07 | archive_ask_single_page | 看一眼 https://blog.example.com/post/9 讲了什么 | search_knowledge 未命中；crawl_webpage 正常；extract_content 置信度 85 | forbid ask_user、save_record、save_to_file（单页速览不问不存） |
| 08 | low_confidence_upgrade | 抓 https://tricky.example.com/article 的正文 | extract_content 返回 LOW CONFIDENCE(score=30)；browse_and_crawl 正常；再次 extract_content 置信度 80 | expect_seq: extract_content → browse_and_crawl → extract_content；forbid save_record（低置信度不许入库不许展示） |
| 09 | mcp_consent | 用抓包工具分析 https://app.example.com 的加密接口 | 评测环境本就无原生 MCP 抓包工具；check_mcp_status 返回 [MCP_UNREACHABLE]；ask_user 答「暂不打开」 | expect_seq: check_mcp_status → ask_user；不许静默拉起、不许甩锅用户「没工具干不了」 |
| 10 | mcp_add_confirm | 帮我加一个 MCP server：name=demo，stdio，command=node server.js | ask_user 答「添加」；add_mcp_server 返回 [MCP_ADDED] | first_tool=ask_user；arg_contains question 含完整配置（command）；add_mcp_server 恰 1 次且在 ask_user 之后 |
| 11 | language_rule | 英文输入 "Please fetch https://plain.example.com/page and tell me what it says" | search_knowledge 未命中；crawl_webpage 返回英文正文 | 自定义 check：回复汉字数 ≥ 20（默认中文回复契约） |
| 12 | chitchat_zero_tools | 你好，你是谁？你能干什么？ | 无 | 总工具调用 == 0 |
| 13 | concept_zero_tools | BeautifulSoup 怎么翻页爬取？给我一个例子 | 无（不含 URL、不含「跑/执行」动词） | 总工具调用 == 0 |
| 14 | weread_direct | 帮我看看这本书的章节 https://weread.qq.com/web/reader/xxx | list_weread_chapters 返回 [WEREAD_COOKIE_NOT_SET] | first_tool=list_weread_chapters；forbid crawl_webpage/browse_and_crawl；后续 ask_user 且 question 含「Cookie」 |
| 15 | batch_first | 把 https://novel.example.com/book/1 的 30 个章节全部抓下来 | search_knowledge 未命中；crawl_webpage 返回章节列表页；run_custom_script 循环抓完打印汇总 | count run_custom_script == 1；总工具调用 ≤ 8（TOOL BUDGET）；不出现逐章循环的 crawl_webpage |

选例原则：每例锚定**一条**可清晰判定的硬规则（检索优先 / 工具选择阶梯 / 失败换路 / ARCHIVE-ASK / 授权先问 / 语言契约 / 零工具对话 / 直达工作流 / 批量优先）；桩输出全部 canned，无真实网络。

### 3.5 常驻契约测试（tests/test_prompt_contract.py，离线）

1. `test_tool_list_matches_registry`：system.md 数字清单的工具名集合与 registry 工具名集合**双向相等**，且 "You have N powerful tools" 的 N 等于 registry 实数。首跑预期红（抓到 §一.3 的两处既有偏差）。
2. `test_hard_rules_present`：关键 HARD 规则标记必须存在（RETRIEVE-FIRST、ARCHIVE-ASK RULE、ASK-USER RULE、BATCH-FIRST RULE、TOOL BUDGET RULE、WORK FOLDER MISSING、CROSS-SESSION ISOLATION、ENVIRONMENT PREREQUISITE RULE、MCP Step 0 工具备查等，以实际标记串为准）——防误删整段规则。
3. `test_tool_doc_no_prereq_hint`：扫描全部 @tool 描述，禁出现「Requires ... installed / 需要先安装」类依赖提示（013 教训固化为 lint，双保险：描述层不许再埋预判拒绝的种子）。

### 3.6 边界与回退

- **LLM 不确定性**：单例偶发失败允许 `--retries` 重跑并标 FLAKY；回归门槛按「同例连续两次 FAIL 才算真回归」人工把握，不做自动抖动抑制（YAGNI）。
- **案例超时**：每例设 wall-clock 上限（初值 180 秒，实施时按首跑分布校准），超时判 FAIL 并注明。
- **跑挂了怎么办**：任一例抛未捕获异常不中断整场，记 FAIL + 异常摘要，最后照常出通过率。
- **评测环境缺 LLM 配置**：启动时先探 get_llm 可用性，不可用直接人话报错退出，不出半份报告。

## 四、涉及文件清单

| 操作 | 文件 | 说明 |
|------|------|------|
| 新增 | `scripts/eval_behavior.py` | harness + 判定器 + CLI + 报告 |
| 新增 | `scripts/eval_cases.py` | 黄金集 15 例（输入、剧本、期望、自定义 check） |
| 新增 | `tests/test_prompt_contract.py` | 离线契约测试 3 条 |
| 修改 | `crawagent/prompts/system.md` | 仅两行既有偏差校正：工具清单补 `fetch_rss_feed` 条目；"21 powerful tools" 改为实数。先红后修（§五.1），顺带验证契约测试有效 |

## 五、验证方式

1. **契约测试有效性（先红后绿）**：只加测试不修 system.md → `test_tool_list_matches_registry` 红（抓到 fetch_rss_feed 缺清单 + 计数 21 过期两处）→ 修 system.md 两行 → 全绿；再临时删除一条 HARD 规则标记 → `test_hard_rules_present` 红 → 还原绿。`test_tool_doc_no_prereq_hint` 在当前工具集上直接绿。
2. **评测首跑基线**：`uv run python scripts/eval_behavior.py` 全量 15 例跑完出报告，基线通过率记入实施记录（报告含逐例序列，FAIL 有人话理由）。
3. **坏规则注入（HANDOFF 指定验收）**：往 system.md 注入「调用任何工具前必须先 ask_user 求许可」→ 重跑评测 → 至少 3 例 FAIL、通过率较基线明显下降 → 撤销注入 → 复跑恢复基线分。证明评测本身测得出提示词劣化。
4. **CLI 冒烟**：`--filter` 单例可跑；`--model` 传当前默认模型跑通任一例。
5. **无副作用**：评测跑完后 `data/sessions/`、`data/knowledge.db`、`output/`、`downloads/` 均无新增（写路径全被桩拦截）。
6. **全量回归**：`uv run pytest tests/ -q` 新基线全绿（预期 767 + 3 = 770，以实施记录实测为准）。

## 六、后续可扩展（不在本次范围）

- **041 用例挂点**：经验复用（查站点档案先读再抓）、失败换路（ERR 后按换 UA → 换代理 → 降级顺序换路）两类用例届时直接加进 eval_cases.py——这正是 040 作为 041 验收底座的用法。
- 附件规则用例（[附件] 块 → read_file 先读再答）：下一批扩充案例时的首选。
- LLM-as-judge 匹配器：自由文本语义断言（评测必要性出现时再上裁判）。
- 多模型对比报告：同一黄金集 × N 模型的矩阵输出。
- **WS 层测试缺口维持挂账**：原计划随 040 复用 agent_smoke WS 基建顺手补，前提不成立（文件不存在、040 改进程内直驱），不再搭车，另行挂账。

## 七、实施顺序建议

1. 契约测试先行：写 tests/test_prompt_contract.py → 首跑确认红在预期两处 → 修 system.md 两行 → 绿（顺带完成 §五.1 的有效性验证）。
2. 评测集：eval_cases.py（15 例输入+剧本+期望）→ eval_behavior.py（桩分发、采集、判定器、CLI、报告）。
3. 首跑全量 15 例，记录基线通过率（§五.2）。
4. 坏规则注入验收 → 撤销复跑（§五.3）。
5. 全量 pytest 回归 + 实施记录随代码入库（git add 只列明确路径）。

---

## 八、实施记录（2026-10-02）

### 怎么做到的（人话版）

这次的核心故事一句话：**给 Agent 造了一把"听话程度"的尺子——真模型、真提示词、真工具说明书，但工具一律是假的（桩），看 LLM 在每个场景下会不会按 system.md 的规矩出牌。**

1. **为什么进程内直驱、不走 WS？** 评测关心的是"LLM 决定调哪个工具"，进程内直接 `agent.invoke()` 就能拿到每一步 tool_calls，不必起服务、解析 UI 事件，还绕开残留进程坑。实施中还发现一个硬约束：ScriptForcerMiddleware 只实现了同步 `wrap_model_call`，`ainvoke` 直接 NotImplementedError——生产 turn_engine 本来就走同步路径，评测跟随同步反而更贴生产。超时用 daemon 线程包一层（同步 invoke 没法中途取消，超时后线程留在后台但进程退出时会回收）。
2. **桩怎么做到"以假乱真"？** 只换工具的执行函数 `func`（并清空 coroutine 防真协程旁路），name/description/参数 schema 原样保留——LLM 看到的工具箱和真的一模一样。每个案例一份"剧本"：哪个工具第几次调用返回什么，`ask_user` 按队列作答。踩过一个坑：初版没编排的工具兜底返回"通用 OK"，案例 01 里模型抓了个寂寞后开始怀疑人生（"所有工具都返回桩响应""这是 IANA 保留域名"），行为完全跑偏——把兜底桩全部升级成拟真输出（抓取返回像样的 HTML、提取返回像样的 Markdown）后跑偏消失。
3. **判定为什么全用确定性规则？** 期望序列（有序子序列）、禁用工具、次数上限、参数包含、回复正则——六种匹配器全部字符串/集合运算，没有"让另一个 LLM 当裁判"。裁判本身有抖动，分数就没法当回归门。代价是表达力有限，用案例级自定义 check 函数兜底（比如"回复汉字数不少于 20"）。
4. **校准的一个例子**：案例 03（SPA 壳换路）最初"禁用 browse_and_crawl"一刀切，实测发现模型 run_custom_script 成功之后又补了一次浏览核验——这不算违规（硬规则管的是"SPA 壳的下一步不许换浏览器工具"），判据校准成"SPA 壳与第一个脚本之间不许插 browse"。阈值类决策都要过一遍真实样本。
5. **这把尺子第一次用就抓到了东西**：基线四跑里三个行为缺陷全程稳定——多页抓完不执行 ARCHIVE-ASK 询问（06）、加 MCP server 前跳过 list_mcp_servers 防重名（10）、WeRead Cookie 未配置时不走 ask_user 索取（14）；另有两个间歇项（01/02 的检索优先顺序漂移）。这些是 040 的第一批收成，行为修复按规格约定另行立项。
6. **模型标签的坑**：`settings.default_model` 存的是 deepseek-v4-flash-vision-exp，但 `get_llm(None)` 经模型池解析实际用的是 glm-5.2——报告标签必须读 `resolve_model()[0]`，不然 JSON 报告署错名。

### 实际改动

| 操作 | 文件 | 说明 |
|------|------|------|
| 新增 | `scripts/eval_behavior.py` | harness + StubBook 桩分发 + 确定性判定器 + CLI（--filter/--model/--out/--retries/--timeout 全实现）+ stdout/JSON 双报告；同步 invoke + daemon 线程超时 |
| 新增 | `scripts/eval_cases.py` | 黄金集 15 例（输入/剧本/期望/自定义 check）+ 拟真兜底桩 DEFAULT_STUBS；HTML 桩用 chr(60)/chr(62) 拼接避开 Write 工具吞尖括号 |
| 新增 | `tests/test_prompt_contract.py` | 契约测试 3 条：清单↔registry 双向相等+计数句、HARD 规则标记存在（HARD_RULE_MARKERS 常量）、工具描述禁依赖提示 lint |
| 修改 | `crawagent/prompts/system.md` | 两行既有偏差校正：工具清单补第 45 条 fetch_rss_feed；"21 powerful tools"改"45" |
| 修改 | `CONTEXT.md` | 新增「工程防护」节 3 术语：行为评测集 / 黄金集 / 契约测试 |
| 新增 | `升级改动文档/040-Agent行为评测集.md` | 本规格 + 实施记录 |

与规格的偏差（均已如实处理）：
- §一.4 预登记：agent_smoke.py 从未入库，"复用其 WS 基建"不成立 → 进程内直驱；WS 层测试缺口随之不再搭车，维持挂账。
- §3.2 写的"func 与 coroutine 都换桩"→ 实现为换 func + coroutine 置空（工具统一是 StructuredTool，异步路径经 executor 走 sync func，效果等同且更简单）。
- §3.4 案例 03 的 forbid 判据 → 校准为自定义 check（见人话版 #4）；案例 15 的 total_max=8 实测够用（四跑最大 8），未放宽。
- §3.5 契约测试首跑红的预言兑现：fetch_rss_feed 缺清单 + 计数句过期，两处全中。
- 兜底桩拟真化属于实施中发现的必要修正（初版会误导模型，见人话版 #2），规格未预见。
- 规格 §3.6 的 180s 超时初值实测合适（最长单例 83.8s），未调。

### 验证结果（对照 §五）

1. ✅ **契约测试先红后绿**：只加测试时红在 fetch_rss_feed 缺清单（计数句检查在其后）；修 system.md 两行后 3 条全绿。负向验证：临时删 WORK FOLDER MISSING 标记 → test_hard_rules_present 红（报「缺硬规则标记」）→ 还原绿。
2. ✅ **评测首跑基线**：glm-5.2（resolve_model 实测），四场完整跑：10/15（66.7%，初版兜底桩）→ 9/15（拟真桩+旧 03 判据）→ 03 校准后合成 **10/15** → 撤销复跑 12/15（80.0%）。基线波动带 60-80%，稳定 FAIL = 06/10/14，间歇项 01/02。
3. ✅ **坏规则注入**：注入「调任何工具前必须先 ask_user 请示」→ 通过率 66.7% → **40.0%**（9 例 FAIL：03/04/05 直接卡死在请示环节，09 双重请示，01/02/06/10/14 违规）→ 撤销后复跑恢复 80.0%。评测测得出提示词劣化，验收成立。
4. ✅ **CLI 冒烟**：--filter 单例（03 校准验证、12 链路验证）可跑；--model 机制经 resolve_model 标签修正验证（默认解析即 glm-5.2）。
5. ✅ **无副作用**：四场评测后 data/sessions/ 无新目录（find -newer 零命中）、无真实网络请求、无写盘（写路径全被桩拦截）。
6. ✅ **全量回归**：uv run pytest tests/ -q → **770 passed**（767 + 3 契约测试）；cd web && npm test → **80 passed**（无前端改动，保险跑）。新基线 **770 pytest + 80 vitest**。

### 说明

- 关键决策数据依据：基线四跑分布（0/15 为 harness 缺陷场不计入；有效三场 9-12/15）支撑「门槛=同例连续两次 FAIL 才算真回归」的抖动对策；03 判据校准、兜底桩拟真化均由实测行为触发。
- 第一批行为发现（06 ARCHIVE-ASK 不执行、10 跳过防重名、14 Cookie 不走 ask_user、01/02 检索顺序间歇漂移）按规格 §二 约定不随 040 修复，建议指挥官拍板是否立项（可并入 041 提示词契约批次）。
- 本变更已随本 commit 入库（代码与文档同批）。
- 临时产物 eval_baseline.json / eval_injected.json / eval_restored.json / eval_*.log 未入库（数字已录本节），确认无需保留后可删除；/tmp 下两份 system.md 注验备份同弃。
- 顺带观察（不属 040 处理）：middleware.py 存在 [ASYNC-MW]/[MIDDLEWARE] 调试 print 残留（awrap_model_call 与同步 wrap_model_call 路径），属变更 031「清理模型调用调试打印」范畴，因多会话并行纪律未动。
