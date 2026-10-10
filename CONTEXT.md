# CrawAgent

LLM 驱动的智能爬虫 Agent 框架。单一上下文：本文件是全项目唯一词汇表，术语定稿即落此处。

## Language

### 抓取过程

**翻页 (Pagination)**:
在同一个列表页的第 1→N 页之间翻动，把列表条目收齐。纯机械工作，可通用化。
_Avoid_: 深度爬取、批量抓取（三者互指会掩盖边界）

**深度爬取 (Deep Crawl)**:
从列表条目跳进各详情页做二次提取。详情页结构千站千面，属语义工作。
_Avoid_: 翻页、递归爬取

**批量抓取 (Batch Crawl)**:
把多页/多条目的整个工作负载合并进一次脚本调用内循环完成（清单自建、翻页自翻、去重、节流、失败汇总）。
_Avoid_: 逐条调用（明确的反模式，上下文会爆）

### 生态扩展

**MCP server**:
CrawAgent 通过 MCP 协议接入的外部能力单元。一条配置 = name + transport（streamable_http / stdio）+ url 或 command+args + 鉴权 headers，存在 `.env` 的 `MCP_SERVERS`（JSON 数组）。构建期由 `build_mcp_tools` 连接并装入原生工具；可 `disabled` 停用（配置保留但不装工具）。
_Avoid_: 把它和"工具"混用——工具是 `@tool` 函数，MCP server 是工具的来源之一。

**CrawAgent skill**:
一个目录里的 `SKILL.md`（带 YAML frontmatter），从 `skills_dirs` + 插件 `skills/` 扫描，构建期注入 system prompt 索引，`read_skill(name)` 读取。是 Agent 的"可安装专长模块"。
_Avoid_: 与 **ZCode 工作流技能**（grill/implement/tdd，在 `~/.agents/`，给 agent 宿主用）和 **runtime skills 模块**（`crawagent/graph/skills.py`，Python 代码）混称——三者都叫"skill"但不是一回事。

**插件 (Plugin)**:
`plugins/` 下带 `plugin.json` manifest 的目录，可同时携带 `tools/*.py`（自动扫描的 @tool）和 `skills/`（CrawAgent skill）。靠文件系统发现，启动期扫描。
_Avoid_: 与 MCP server 混——插件是本地代码包，MCP server 是协议端点。

### 逆向与抓包

**Android 逆向 / PC 逆向**:
用 Frida hook 定位 App / Windows 桌面应用的加密点（变更 047 补齐 PC 侧）。Android 侧 6 工具：`list_adb_devices` / `install_apk` / `push_file` / `frida_hook_function` / `frida_dump_so` / `frida_bypass_ssl_pinning`；PC 侧 4 工具：`list_windows_processes` / `frida_hook_pc_function` / `frida_dump_dll` / `frida_bypass_pc_ssl`。绕过 SSL pinning 后接 MCP 抓包链路还原明文通信。
_Avoid_: 与 **MCP 抓包分析**混——逆向负责定位/绕过加密，抓包链路负责还原明文；也勿与浏览器的"渲染抓取"（browse_and_crawl）混。

### 会话与产物

**工作文件夹 (work_dir)**:
会话级产物落点（变更 034）：新建对话时由用户弹系统原生对话框选定，存 meta.db `session_titles.work_dir`，随首条 WebSocket 消息 `work_dir` 字段入库。配置后该会话全部产物（文本/媒体/脚本）直接落该文件夹——不套会话子目录，媒体保留工具分类子目录（images/ 等），同时它是路径安全校验的边界（写到文件夹外即拒绝）。
_Avoid_: 与 014 的"会话产物子目录"（`output/<会话名>/`，无 work_dir 时的兜底默认）混用；与设置页全局"产物目录"混用（那是无会话粒度的根）。

**档案入库询问 (ARCHIVE-ASK)**:
抓取类任务完成时 AI MUST 用 ask_user 问「本次抓取的内容要存入档案吗？」（存入档案 / 不用 / 本会话不再询问），用户拍板后才 `save_record` 入知识库。取代旧行为"抓取成功即静默 save_record"。"本会话不再询问"由对话上下文自持，不落库。
_Avoid_: 跳过询问直接入库；对单页速览 / 失败任务 / 用户已拒绝过的会话重复弹问。

**会话文件夹 (Session Folder)**:
会话级记忆/档案的物理落点（变更 039，ADR-0008）：`data/sessions/<会话id>/`，sid 全值命名（非 014 截断），惰性创建，结构 `archive/<主题>.md`（档案快照，save_record 双写落盘）+ `memory/`（预留）。人可读、可整个拷走备份、清理 = 删目录。
_Avoid_: 与 **工作文件夹**（用户项目产物落点，可被删/失效）混——会话文件夹是系统资产、与项目物理解耦；与 014 的"会话产物子目录"（output/<会话名>/，标题清洗名）混。

**长期记忆 (Long-term Memory)**:
会话提炼归档后入库的知识条目（变更 039）：LLM 蒸馏超期会话文件夹的 md 内容成主题+要点条目，直插 crawl_records（platform=长期记忆、session_id=来源会话、url=session://<sid>，免五关质量过滤），search_knowledge 可检索。
_Avoid_: 与 checkpointer 的轮次历史（不迁移不提炼）、与爬取记录（走五关）混。

**归档 (_archived)**:
`data/sessions/_archived/`，提炼入库成功后整个会话文件夹的移动终点。仅移动、永不自动物理删除——删除永远由用户手动执行；蒸馏失败不动文件夹（宁留勿丢）。
_Avoid_: 把它当回收站（里面是已提炼完的精华备份，不是垃圾）。

### 工程防护

**行为评测集 (Behavior Eval Set)**:
Agent 行为回归尺（变更 040）：`scripts/eval_behavior.py` 进程内直驱真 Agent（真 system.md + 真工具 schema + 真中间件），工具执行换桩，黄金集逐例判定输出通过率。改 system.md / 换模型前后各跑一遍当回归门；顺序执行、不进 pytest（要真 LLM）。基线 glm-5.2 约 10/15（波动带 60-80%）。
_Avoid_: 拿它当 CI 常驻（花钱且有 LLM 抖动，门槛按「同例连续两次 FAIL 才算真回归」人工把握）；给判定器上 LLM-as-judge（判定必须确定性可复现）。

**黄金集 (Golden Set)**:
20 条「用户输入 + 桩剧本 + 期望」用例（`scripts/eval_cases.py`，040 建 15 例、041 扩至 20 例），每例锚定 system.md 一条硬规则（检索优先 / 经验复用 / 失败换路 / ARCHIVE-ASK / MCP 授权 / 语言契约 / 零工具 / 批量优先等）。桩输出全部 canned，无真实网络、不写真实 data/。
_Avoid_: 一例测多条规则（判定失败时说不清是哪条坏了）；剧本里编排真实域名期待真数据（全部走 stub）。

**契约测试 (Prompt Contract Test)**:
system.md 与代码的静态一致性检查（`tests/test_prompt_contract.py`，离线零成本随 pytest 常驻）：工具清单与 registry 双向相等 + 计数句一致、HARD 规则标记存在、工具描述禁依赖提示（013 教训 lint 化）。
_Avoid_: 与行为评测混——契约测试防「文档与代码漂移」，行为评测防「LLM 不按文档行事」；往 system.md 加规则时忘了同步 HARD_RULE_MARKERS 清单。

**经验闭环 (EXPERIENCE LOOP)**:
抓前抓后的经验硬规则（变更 041，system.md）：抓任何域前先 list_site_profiles 查站点档案（FOUND 带脚本就直接跑档案脚本，不重新分析）、内容型请求再 search_knowledge 查跨会话档案；新站抓成功后问一次才 save_site_profile（速览/失败/已拒绝不问），跑通的脚本必存档案。
_Avoid_: 把爬完之后补查 list_site_profiles 当作已履约（时序不算数）；把站点档案询问与 ARCHIVE-ASK 混为一谈（前者存策略与脚本，后者存内容，两问允许合并但语义独立）。

**失败自愈阶梯 (SELF-HEAL LADDER)**:
内置抓取工具 ERR 后的换路硬规则（变更 041，system.md）：禁止同工具同 URL 同参数原样重试，按 L1 换 UA（脚本 S1）→ L2 换代理（get_proxy，失败立即 mark_proxy_failed）→ L3 静态/浏览器互换 或 P 路由 的顺序换路，单目标总尝试至多 6。SPA 壳 / LOW CONFIDENCE / paywall 三类例外走各自既有规则。
_Avoid_: 把 ERR 后的 browse_and_crawl 一次当成万能兜底（阶梯第一格是脚本换 UA）；与 PIONEER MINDSET 的 10 条备选路由重复处理（阶梯只负责换变量，路由枚举归 PIONEER）。
