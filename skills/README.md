# CrawAgent 内置爬虫技能包

给 CrawAgent Agent 用的 SKILL.md 技能集。每个子目录一个技能，`SKILL.md` 的
frontmatter（name + description）在 Agent 构建时注入 system prompt 索引，
正文由 Agent 通过 `read_skill(name)` 按需读取（渐进披露，不膨胀上下文）。

## 技能清单

| 技能 | 触发场景 | 内容 |
|---|---|---|
| `bilibili-grab` | bilibili.com / BV号 / b23.tv 链接 | 元数据/评论/下载路由、Cookie 与画质、DASH 合流坑、批量清单 API |
| `douyin-grab` | 抖音链接 / v.douyin.com 分享短链 | 短链解析、无水印直链、图集、移动 UA、批量与风控节流 |
| `wallpaper-sites` | 壁纸站 / 图片站 | 专用工具链（extract_wallpaper_list）、防盗链 referer、背景图卡片配方 |
| `weread-grab` | 微信读书 | Cookie 存档、章节列表→正文流程、整本批量导出、VIP 章节姿势 |
| `batch-crawl-playbook` | 「全部/所有/批量/top N」类任务 | BATCH-FIRST 落地：一次脚本循环模板八要点、断点续抓、失败汇总 |
| `custom-script-recipes` | 预制工具失败、要写自定义脚本时 | helper 速查表、S1–S4 各级骨架、诊断对照表、预算纪律 |

## 使用

技能随 Agent 构建自动生效，无需配置。`skills_dirs`（`.env` 可覆盖，默认 `skills`）
指定本目录；外部技能目录用分号追加，如 `SKILLS_DIRS=skills;D:/my-skills`。

## 新增技能

1. 建目录 `skills/<skill-name>/SKILL.md`
2. frontmatter 写 `name`（kebab-case）与 `description`（一句话，≤200 字符，
   前置触发词 —— 它会被逐字注入 system prompt）
3. 正文放操作步骤与站点事实；深度材料放 `references/*.md`，Agent 会用
   `read_skill(name, ref=...)` 读取
4. 重启后端生效（技能索引是构建期快照）

写 SKILL.md 的规范：描述前置触发词、正文步骤优先、全局规则（BATCH-FIRST、
S1-S4 阶梯等）引用 system.md 而不重复、事实必须与工具实现一致。
