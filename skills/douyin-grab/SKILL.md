---
name: douyin-grab
description: 抖音/douyin/v.douyin.com 分享链接抓取攻略：视频与图集解析、无水印地址、评论、移动UA短链重定向、批量下载。拿到抖音链接或说「抖音视频/图集」时先读我。
---

# 抖音抓取攻略

## 路由判断

| 用户意图 | 正确路径 |
|---|---|
| 单条作品（视频/图集）的元数据、评论、媒体 | `extract_social_media(url, fields)` |
| 明确要求保存/下载 | `download_social_media(url, include, subdir)` |
| 批量（用户主页全部作品 / 话题合集） | 本技能「批量模式」 |

抖音和 B 站共用同一组入口工具 —— 用户给链接（含 `v.douyin.com` 分享短链）直接调，**不要**先 crawl_webpage 或 browse_and_crawl。

## 短链与 ID 解析（底层行为，自写脚本时要复刻）

- 工具内部的 aweme_id 提取顺序：`/video/<id>` → `/note/<id>` → `/slides/<id>` → `/share/video/<id>` → `modal_id=` → `aweme_id=` → `item_ids=`
- 短链解析：`v.douyin.com/xxxx` 必须带**移动端 UA** 跟 302 重定向，落地页才是 `/share/video/<id>`
- 图集（note）和幻灯（slides）与视频同一个 ID 体系

## 数据来源（免登录）

1. 分享页 HTML：`https://www.iesdouyin.com/share/video/<id>`，**移动端 UA**，正则提 `window._ROUTER_DATA = {...}` JSON —— 视频地址、图集、统计都在里面
2. 兜底 API：`https://www.iesdouyin.com/web/api/v2/aweme/iteminfo/?item_ids=<id>`（老接口，时灵时不灵；先试 1 再试 2）
3. 评论：`https://www.iesdouyin.com/web/api/v2/comment/list/?aweme_id=<id>`（免登录，热门评论，数量有限）

抖音风控迭代快：上述路径失败时直接进 `run_custom_script` 的 S1→S4 阶梯（见 `custom-script-recipes` 技能），别在预制工具上重复撞墙。

## 无水印与图集

- `_ROUTER_DATA` 里 `video.play_addr.url_list[0]` 拿到的地址把 `playwm` 换成 `play` 即无水印（自写脚本配方；`download_social_media` 已内置处理）
- 图集作品：`images` 数组每项的 `url_list` 是原图直链，`include="images"` 会批量下载
- 图集与视频的判别：metadata 里 `images` 非空即图集

## 下载细节（download_social_media）

- `include` 可选：`video, audio, cover, images, comments`；视频 MP4 自带音轨，**不需要** ffmpeg 合流（这是和 B 站的关键差异）
- 保存位置：`downloads/<platform>_<aweme_id>/`，工具自动命名
- 直链下载有防盗链：自写脚本下载时 UA 要与解析时一致（移动端 UA），并带 `Referer: https://www.douyin.com/`

## 批量模式

用户主页作品清单（一次脚本拿完，别逐条调工具）：

```python
# 抖音用户主页是 SPA + 风控最重的页面 —— 直接 browser_render（S4 先行是合理的，
# 这里 S1 拿不到东西）
sec_uid = "从主页 URL 或分享页 _ROUTER_DATA 的 author.sec_uid 拿"
url = f"https://www.iesdouyin.com/share/user/{sec_uid}"
final_url, html = browser_render(url, wait_ms=5000)
# 从渲染后 HTML 正则提 aweme_id + 标题；翻页走 scroll 或调用主页 API（需签名，成本高）
```

- 主页 API（`/aweme/post/`）需要 X-Bogus / a_bogus 签名 —— **不要**自己逆签名，超过 S4 能力直接告知用户「需登录 Cookie 或抓包分析」，并建议走 MCP 抓包流程拿签名参数
- 清单到手后批量下载：一个脚本循环所有直链 `download_to_file`，带移动 UA + Referer

## 已知坑速查

- 抖音高频请求会触发滑块验证 —— 同一 IP 连续抓 20+ 条后直链可能 403；脚本里 `time.sleep(random.uniform(1, 3))` 节流
- 直链有时效（几小时内失效）—— 先解析后立刻下载，别把直链存档当永久链接
- 直播/合集/收藏夹链接不在支持范围，明确告知用户
