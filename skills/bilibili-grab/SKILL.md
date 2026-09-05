---
name: bilibili-grab
description: B站/bilibili/BV号/b23.tv 链接的抓取攻略：视频元数据、评论、封面、弹幕式批量下载、画质与 Cookie、ffmpeg 合流的坑。拿到 bilibili.com 或 b23.tv 链接时先读我。
---

# B 站（bilibili）抓取攻略

## 路由判断（先做这个）

| 用户意图 | 正确路径 |
|---|---|
| 单个视频/图集的元数据、评论、媒体地址 | `extract_social_media(url, fields)` 一步到位 |
| 明确要求下载/保存 | `download_social_media(url, include, subdir)` |
| 批量（合集/分区/UP主全部投稿，>3 条） | 本技能「批量模式」—— 一个 `run_custom_script` 循环 |
| 只想要 UP 主全部视频的**清单**（不下载） | `run_custom_script` 调空间 API（见下） |
| 需要看真实 API 请求/签名分析 | MCP 抓包流程（见 system.md 的 MCP Capture Workflow） |

用户直接丢链接（含 `b23.tv` 短链）时，**不要**先 crawl_webpage —— 直接 `extract_social_media`，它内部会解析短链、拿 BV 号、调官方 API。

## 免登录能拿什么（extract_social_media）

- **metadata**：标题、简介、BV 号、aid、up主、时长、分区、播放/点赞/投币/收藏/分享数、发布时间
- **comments**：热门一级评论（走 `api.bilibili.com/x/v2/reply/main`，免登录可用，数量有限）
- **media**：视频流地址（DASH 分离的 video+audio URL）与封面图

## Cookie 的真实作用

- 无 Cookie：能解析、能下载，但**视频流默认最低清晰度（360p 附近）**
- `.env` 配了 `BILIBILI_COOKIE` → 自动带上请求头，清晰度可到登录档位（1080p）
- 1080P 高码率 / 4K / 大会员画质仍需大会员账号的 Cookie —— 拿不到时如实告知用户，别死磕
- 用户给了新 Cookie → 存进站点档案是没用的（这个工具读的是 `.env` 的 BILIBILI_COOKIE），要提醒用户更新 `.env` 后重启后端

## 下载的坑（download_social_media）

1. **B 站 DASH 流是视频、音频分离的** —— 工具会自动下两路并用 ffmpeg 合流。
2. 合流依赖 ffmpeg：返回 JSON 里若带「ffmpeg not found」类 note，说明视频/音频只分开保存了。修复方式：装 ffmpeg（`FFMPEG_PATH` 或 PATH）。**告知用户文件是分离的之前先看 note 字段**。
3. 封面是直链图，`include="cover"` 即可拿到；要批量下封面用 `download_images`（记得 referer 传 `https://www.bilibili.com`，B 站图床有防盗链）。
4. 弹幕不在 fields 里 —— 用户要弹幕走 `run_custom_script`：`https://api.bilibili.com/x/v1/dm/list.so?oid=<cid>`（protobuf/deflate，用 `requests` + `dandanplay` 式解析或直接存原始文件）。

## 批量模式（合集/UP 主投稿）

先用一次 `run_custom_script` 拿**清单**（不要逐个调 extract_social_media）：

```python
# S1：UP 主全部投稿（分页循环，一次脚本拿完）
mid = "UP主的mid"   # 从其空间页 URL 或 view API 的 owner.mid 拿
base = f"https://api.bilibili.com/x/space/wbi/arc/search?mid={mid}&ps=30&pn={{page}}"
# 该接口要 wbi 签名；签名拿不到就直接抓空间页 HTML 正则提 BV 号，
# 或用浏览器渲染 browser_render("https://space.bilibili.com/<mid>/video") 后 bs4 提 <a href="/video/BV...">
for page in range(1, pages + 1):
    r = requests.get(base.format(page=page), headers=headers)
    # 收集 (bvid, title) 到 results
print(json.dumps(results, ensure_ascii=False))
```

清单到手后：
- 用户只要清单 → `save_to_file` 交差
- 用户要批量下载 → **再写第二个批量脚本**逐条调 playurl 下载，或逐条调 `download_social_media`（每条 1 次调用是合理的，因为每条视频要 ffmpeg 合流；**仍然不要**对每条视频重复 extract_social_media + download 两次调用）

## 已知坑速查

- `b23.tv` 短链必须跟 302 重定向才能解析（工具已处理；自写脚本要 `allow_redirects=True` + 移动 UA）
- 分 P 视频（多 parts）：view API 返回 `pages` 数组，每个 part 有独立 cid；`download_social_media` 默认只下 P1 —— 用户要全 P 时用脚本循环 `pages`
- 番剧/影视（ss/eid 开头的 ep 链接）不走普通视频 API，需要大会员 Cookie 且接口不同 —— 先声明局限
- 评论有全局页码上限（免登录态一般只能拿前几页热门），用户要「全部评论」时明确说明拿不到
