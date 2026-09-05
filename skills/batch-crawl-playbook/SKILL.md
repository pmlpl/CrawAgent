---
name: batch-crawl-playbook
description: 批量抓取总配方：多章节/多页/多条目任务合并为一次脚本循环的完整模板、进度输出、断点续抓、失败汇总、落盘规范。用户说「全部/所有/批量/top N/每一页」时先读我。
---

# 批量抓取总配方（BATCH-FIRST 落地手册）

system.md 的 BATCH-FIRST 硬规则只给了一句原则；本技能给落地配方。触发词：用户说 **全部 / 所有 / 整本 / 批量 / top N / 每一页 / 每一章 / 列表里每一项**。

## 为什么必须合并

每多一次工具调用 = 一份完整工具输出进上下文 + 一次模型往返。30 条逐条抓 = 上下文爆炸 + 轮次超时 + 用户看着进度条干瞪眼。一个循环脚本 = 1 次调用，进程内全部搞定。

## 模板（按任务改造，不是照抄）

```python
import json, time, random
from pathlib import Path

# ① 目标清单：URL/ID 列表最好在脚本内构造（分页循环自己翻），不要让外层传 30 个参数
targets = []
for page in range(1, 4):                       # ② 分页自行循环
    r = requests.get(f"https://example.com/list?page={page}", headers=HEADERS, timeout=20)
    targets += [urljoin("https://example.com", a["href"]) for a in BeautifulSoup(r.text, "html.parser").select("a.item")]
targets = list(dict.fromkeys(targets))         # ③ 去重保序
print(f"PLAN: {len(targets)} items")           # ④ 开跑前先报计划量

ok, fail = [], []
OUT = OUTPUT_DIR / "任务名"                     # ⑤ 文本 → output/；媒体 → downloads/
OUT.mkdir(parents=True, exist_ok=True)
for i, t in enumerate(targets, 1):
    try:
        item = requests.get(t, headers=HEADERS, timeout=20)
        item.raise_for_status()
        (OUT / f"{i:03d}.md").write_text(parse(item.text), encoding="utf-8")
        ok.append(t)
        print(f"[{i}/{len(targets)}] OK len={len(item.text)}")   # ⑥ 逐条进度行
    except Exception as e:
        fail.append((t, str(e)[:80]))
        print(f"[{i}/{len(targets)}] FAIL {e}")
    time.sleep(random.uniform(0.5, 1.5))       # ⑦ 节流防风控

print(f"SUMMARY: ok={len(ok)} fail={len(fail)}")                 # ⑧ 收尾汇总
if fail: print(json.dumps(fail, ensure_ascii=False))             # 失败明细给外层接手
```

八处编号就是质量清单：**清单自建 / 分页自翻 / 去重 / 开跑报量 / 落盘目录正确 / 逐条进度 / 节流 / 失败汇总**。

## 断点与重试

- 单条失败**不中断**整体循环（模板已如此），失败清单最后打印，外层决定要不要补抓
- 大批量（>100 条）分两次脚本调用：第一次跑一半并 print 已完成游标；第二次从游标续 —— 脚本超时上限 120 秒，60 条/次是安全线（视单条耗时）
- 下载类任务先 HEAD 探存活再 GET，或 `stream=True` 边下边写

## 落盘与汇报规范

- 文本/结构化 → `OUTPUT_DIR/<任务名>/`；图片视频 → `DOWNLOADS_DIR/<任务名>/`（system.md 下载路径硬规则）
- 收尾汇报给用户的格式：总量、成功数、失败数与原因分类、保存路径 —— 一段话，别贴 30 行明细
- 涉及新站点且脚本有效 → `save_site_profile(script=脚本, strategy="custom_script")` 存档，下次同站直接复用

## 例外：什么时候允许逐条调用

- 每条需要**独立的复杂交互**（ffmpeg 合流、浏览器渲染交互、需用户确认的 VIP 内容）—— 此时逐条是物理必要的，但仍先批量拿清单
- 条目数 ≤ 3 —— 开销可忽略
