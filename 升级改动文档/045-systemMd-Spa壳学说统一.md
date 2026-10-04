# 变更 045：system.md SPA 壳学说统一——消除契约自相矛盾，03 号用例定档

| 项目 | 内容 |
|------|------|
| 变更编号 | 045 |
| 提出日期 | 2026-10-04 |
| 状态 | 已完成（2026-10-04） |
| 类型 | bug修复 / 评测治理 |
| 关联模块 | `crawagent/prompts/system.md`（2 行矛盾文本清理）、`tests/test_prompt_contract.py`（回归锁） |

---

## 〇、价值

本变更让「行为评测的尺子重新可信」并让 agent 行为回归确定——改动前：system.md 里两套 SPA 壙处理学说并存（一套说空壳后禁止浏览器、另一套说空壳就换浏览器），agent 按哪套走全看模型当轮心情，03 号评测用例连续两场 FAIL（040 门槛：同例连续两次 FAIL），每次回归测试都误报；改动后：契约只剩一套学说，agent 行为确定，03 号回到连续 PASS，考卷恢复判分公信力。

## 一、背景与问题

040 评测 03 号用例（spa_shell）**连续两场 FAIL**：044 实施会话主跑 16/20 挂它、独立验收会话复跑 18/20 又挂它，踩中 040 立的「同例连续两次 FAIL」立项门槛。

**规格期实锤（两场失败的完整调用流水）**：agent 的实际行为是 `list_site_profiles → crawl_webpage（返回 SPA 壳）→ browse_and_crawl（浏览器渲染）→ extract_content（拿到完整正文）→ ask_user`——**任务完成了、回复质量 82 分、ARCHIVE-ASK 也履行了**，唯独「空壳后换浏览器」这一步违反了考卷判据 `expect_seq=["crawl_webpage", "run_custom_script"]` 和 `_check_no_browse_before_script`。两场失败模式一致，不是抖动噪声。

**根因：system.md 契约自相矛盾**，四行文本两套学说对峙：

| 行 | 文本要点 | 来历 |
|----|----------|------|
| L67 | SPA 壳 = FAILURE，下一步**禁止** browse_and_crawl，直接 run_custom_script | 040/041 既定学说（HARD） |
| L125 | 041 失败换路阶梯的显式豁免：「Exceptions (OVERRIDE): SPA shell → run_custom_script directly (browse_and_crawl forbidden)」 | 041 有意保留的例外条款 |
| L56 | 「TOOL CHOICE: single JS/SPA page → **browse_and_crawl**」 | **57f919f 远古残留**（插件化重构批次） |
| L222 | 「If crawl_webpage returns an SPA shell < 5KB **switch to browse_and_crawl**」 | **57f919f 远古残留** |

L56/L222 早于 040 学说数周、无任何测试锚定（grep tests/ 与 eval_cases.py 零命中）。模型读到矛盾契约时按哪套走随当轮采样波动——041 时代评测 17/20 时 03 号恰好看的是 L67，如今两轮恰好看的是 L56/L222。**考卷没错，错的契约是 agent 的。**

## 二、目标

1. system.md 统一 SPA 壳学说为既定的「脚本优先」（保 L67/L125，清 L56/L222 残留）。
2. 03 号用例回到连续两跑 PASS（040 门槛正向使用）。
3. 同学说锚定的 04 号（2 连败后必须 run_custom_script）、05 号（禁止 browse_and_crawl）用例不受伤。
4. 加回归锁：契约测试禁止两处残留文本回潮。

**不做**（划界）：
- **不重估学说本身**（「SPA 壳到底该脚本还是浏览器」是 040/041 已拍板的既定决策，本变更只消除执行层矛盾；将来若要翻转学说须另行立项并重校 03/04/05 全部相关用例）。
- 不改 03 号判分（expect_seq / _check_no_browse_before_script 与统一后学说一致，保持原样）。
- 不动 044 中间件、不改 042 审计、不碰 auto-title（另挂账）。

## 三、方案设计

### 3.1 L56 修正（TOOL CHOICE 导引）

现文：`single static page → crawl_webpage; single JS/SPA page → browse_and_crawl; ...`

改为：`single page → crawl_webpage first (JS/SPA included — if it returns a shell, follow the SPA rule: run_custom_script, never browse_and_crawl); whole site/docs → crawl4ai_deep_crawl; ...`

（保留原行其余部分：crawl4ai / browser_use_navigate 的分工与「最重工具勿轻用」告诫不动。）

### 3.2 L222 修正（反 API 瞎猜规则里的顺手误导）

现文：`If crawl_webpage returns an SPA shell < 5KB switch to browse_and_crawl; do NOT hunt for API endpoints yourself.`

改为：`If crawl_webpage returns an SPA shell < 5KB go straight to run_custom_script (SPA rule); do NOT hunt for API endpoints yourself.`

（该行主旨是「禁止瞎猜内部 API」，只修顺带的 SPA 指引，其余不动。）

### 3.3 回归锁（tests/test_prompt_contract.py 新增 1 例）

```python
def test_no_stale_spa_contradiction():
    text = _read_system_md()
    assert "switch to browse_and_crawl" not in text      # L222 残留
    assert "single JS/SPA page → browse_and_crawl" not in text  # L56 残留
    # 既定学说必须仍在（防反向回潮）
    assert "must NOT be browse_and_crawl" in text        # L67 硬规则原文
```

阈值/参数：无（纯文本契约）。

## 四、涉及文件清单

| 操作 | 文件 | 说明 |
|------|------|------|
| 修改 | `crawagent/prompts/system.md` | L56/L222 两处矛盾文本按 §3.1/3.2 修正 |
| 修改 | `tests/test_prompt_contract.py` | 新增 `test_no_stale_spa_contradiction` 回归锁 |

## 五、验证方式

1. **契约测试**：`uv run pytest tests/test_prompt_contract.py -q` 新例全绿；全量 `uv run pytest tests/ -q` 833→834 全绿。
2. **评测两跑（040 门槛正向）**：`uv run python scripts/eval_behavior.py` 跑两遍，03 号两连 PASS；整体分数不低于基线带下沿 16/20；04 号、05 号两跑全 PASS。
3. **真机观察（可选）**：挑一个已知 SPA 站发抓取任务，trace 确认空壳后走 run_custom_script 而非 browse_and_crawl。
4. 每条验收「怎么做、看到什么算过」已具体化，实施后逐条打勾如实汇报，不写「功能正常」。

## 六、后续可扩展（不在本次范围）

- 学说翻转评估（若未来 browse_and_crawl 成本降到与脚本同级）：需重校 03/04/05 三用例 + system.md L67/L125，单独立项。
- 18 号老间歇（两场也挂但属挂账家族）：随本批观察，不承诺修。

## 七、实施顺序建议

system.md 两行 → 契约回归锁 → 全量 pytest → 评测两跑 → **交实施会话执行（主会话按新分工不亲自实施）** → 独立验收。

---

## 八、实施记录（2026-10-04）

### 怎么做到的（人话版）

system.md 里关于「抓到 SPA 空壳怎么办」其实一直有两套说法打架：一套是硬规则（说空壳算失败、禁止换浏览器、直接上脚本），另一套散落在别处说「单页 SPA / JS 页 / 拿到壳就换浏览器」。模型读到矛盾契约时按哪套走全看当轮采样——03 号评测用例（SPA 壳任务）就这样连续两场 FAIL。这次把三处远古残留统一到既定的「脚本优先」学说：工具选择导引改成「单页一律先 crawl_webpage，拿到壳就走脚本、别碰浏览器」；详情页流程把「JS 渲染页」从浏览器降级的触发条件里摘出来、明确指回脚本规则（普通的抓取失败换浏览器兜底保留）；反 API 瞎猜规则里顺带的误导改成「直奔脚本」。再加一条契约测试：三处残留短语不得回潮、既定硬规则不得被删。

### 实际改动

| 操作 | 文件 | 说明 |
|------|------|------|
| 修改 | `crawagent/prompts/system.md` | 三处 SPA 残留统一到「脚本优先」学说（规格 §3.1/§3.2 两处 + 实施期实锤的第三处 L179，见说明 #1） |
| 修改 | `tests/test_prompt_contract.py` | 新增 `test_no_stale_spa_contradiction` 回归锁（§3.3 断言原文照抄；helper 名适配现有 `_system_md()`） |

### 验证结果（对照 §五）

1. **契约测试**：`uv run pytest tests/test_prompt_contract.py -q` **4/4 全绿**。新回归锁经历「先红后绿」——锚定 L179 残留时红、清后转绿，锁真的在咬。全量 pytest **834 全绿**（833→834，+1 为新回归锁）。
2. **评测两跑**（glm-5.2，JSON 留存 `eval_045_run1.json` / `eval_045_run2.json` / `eval_045_04_retry.json`）：
   - run1 **17/20 (85.0%)**：03 PASS、04 PASS、05 PASS；FAIL = 15/18/20。
   - run2 **17/20 (85.0%)**：03 PASS、05 PASS；FAIL = 04（超时）/15/18。
   - **03 号两连 PASS**——规格核心目标达成（044 期连续两场 FAIL → 改后两场全 PASS，流水均为脚本先行：run2 为 `crawl_webpage → run_custom_script → extract_content` 教科书式）；总分两跑均 ≥16 达标。
   - 04 号 run2 超时 FAIL：流水 `crawl_webpage → browse_and_crawl → run_custom_script → 脚本×4`——2 败后走脚本方向正确，但连打 4 次脚本烧光 180s（与 041 挂账「桩输出不满意模型连打脚本」同族方差；045 未动 04 号相关文本）。**补跑单例 PASS**（22.4s，干净流水）——三跑两 PASS，判定非学说回归、采样方差。
   - 15/18 两跑 FAIL：041 已挂账间歇家族（判据是 run_custom_script/save_site_profile 调用次数，与 SPA 学说无涉）；20 号 run1 FAIL / run2 PASS 间歇。随本批观察不承诺修（与 §六 一致）。
3. **真机观察（可选）**：未做，留指挥官。

### 说明

1. **与规格的偏差——L179 第三处同族残留一并统一**：规格 §四 只列 L56/L222 两处，但 §3.3 回归锁的锚 `"switch to browse_and_crawl" not in text` 实施时红了第三处——**L179（DETAIL-page workflow 第 2 步「Incomplete result / JS-rendered page / failure → switch to browse_and_crawl」）经 git blame 实锤与 L56/L222 同为 57f919f（2026-09-05 插件化重构）引入的远古残留**，且正是 03 号实测失败路径（单页内容任务拿壳→换浏览器）的直接行为引导。按「§3.3 原文照抄」的交接要求与 §〇「契约只剩一套学说」的价值声明一并统一：JS 渲染/SPA 壳场景显式指回脚本规则；普通 failure 降级保留为「re-fetch with browse_and_crawl」（与 L172 句式一致，不伤 041 SELF-HEAL LADDER / SUPERVISOR 的浏览器兜底条款）。
2. 回归锁 helper 名：规格伪代码写 `_read_system_md()`，落地适配该文件现有 helper `_system_md()`，断言三行原文照抄。
3. **评测实施事故**：第一次启动的评测跑因启动命令挂了 `| tail -40` 导致 stdout 明细截断（仅存总分 14/20 与 19/20 号 PASS，FAIL 集合不可归因），作废后带 `--out` 重跑为正式 run1——多花一轮 LLM 费用。教训：评测启动命令禁止 tail 截断，明细以 `--out` JSON 为准。
4. system.md 是构建期读盘：评测为进程内直驱新起进程、现读改动即时生效；跑着的 8006 主服务须重启后才吃到新契约（未重启，留指挥官）。
