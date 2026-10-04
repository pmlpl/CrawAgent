# 变更 045：system.md SPA 壳学说统一——消除契约自相矛盾，03 号用例定档

| 项目 | 内容 |
|------|------|
| 变更编号 | 045 |
| 提出日期 | 2026-10-04 |
| 状态 | 待实施 |
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
