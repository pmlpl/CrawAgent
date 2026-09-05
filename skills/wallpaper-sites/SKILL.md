---
name: wallpaper-sites
description: 壁纸站/图片站通用抓取攻略：CSS背景图卡片、动态壁纸识别、自动翻页、详情页原图、防盗链referer、批量下载配方。任务涉及 wallpaper/壁纸/图片站/4kwallpaper/极简壁纸等时先读我。
---

# 壁纸站 / 图片站抓取攻略

## 铁律：用专用工具，不用通用列表工具

壁纸站的卡片是 **CSS background-image + JS 渲染**，不是 `<img>` 标签，也不是 `<a>` 文字链：

- `extract_list` 在这类站上**必然漏抓**（只看 `<a>` 文字链）
- 正确入口：`extract_wallpaper_list(url, limit, exclude_dynamic)` —— 自动翻页、进详情页、识别静态/动态
- 单条详情：`wallpaper_detail(detail_url)`
- 批量落盘：`download_images(urls, subdir, referer)`

## extract_wallpaper_list 的行为细节

1. 自动翻页：按「下一页」链接扫列表页，收集详情页链接，凑够 `limit*2`（排动态时 `limit*4`）条才停 —— 所以 limit 是**期望返回数**，不是翻页数
2. 每条返回：STATIC/DYNAMIC 类型、标题、分辨率、缩略图、详情页 URL、全部图片/视频 URL
3. `exclude_dynamic=True`：跳过视频动态壁纸（用户说「不要动态/只要静态图」时传 true）
4. 分辨率取自详情页文本（如 3840x2160），拿不到就是 unknown —— 别编造

## 防盗链（下载失败第一原因）

图片直链直接 requests 会 403 —— 站点校验 Referer：

- `download_images(urls, subdir, referer=站点根URL)` —— referer 参数必传，如 `https://www.wallhaven.cc`
- 自写脚本：`headers = {"Referer": 站点根, "User-Agent": 桌面UA}`
- 判别方法：浏览器能开图、requests 403 → 就是防盗链，补 referer 即可

## 典型工作流（用户：「下载前 20 张 4K 风景壁纸」）

```
1. extract_wallpaper_list(分类页URL, limit=20, exclude_dynamic=True)
2. 从结果收集图片直链（优先原图字段而非缩略图）
3. download_images(urls, subdir="wallhaven_4k", referer=站点根)
4. 汇总：成功 N 张、失败列表、保存路径
```

这是 3 次工具调用的事。**绝不**逐张壁纸调一次工具 —— 批量任务遵循 system.md 的 BATCH-FIRST 硬规则；若工具翻页凑不齐或站结构特殊，一个 `run_custom_script` 内循环搞定（见下）。

## run_custom_script 通用配方（工具失败时）

```python
# S1 通用壁纸站脚本：列表页提详情链接 → 循环详情页提原图
headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) ...", "Referer": "https://<站点根>"}
r = requests.get("<列表页URL>", headers=headers)
soup = BeautifulSoup(r.text, "html.parser")
details = []
for a in soup.select("a[href]"):
    # 壁纸站详情链接通常含 /wallpaper/ /photo/ /image/ /post/ 等路径段
    if re.search(r"/(wallpaper|photo|image|post|pic)/\d+", a["href"]):
        details.append(urljoin(r.url, a["href"]))
details = list(dict.fromkeys(details))[:30]
for d in details:
    dr = requests.get(d, headers=headers)
    dsoup = BeautifulSoup(dr.text, "html.parser")
    for tag in dsoup.select("img, [style*=background-image]"):
        src = tag.get("src") or tag.get("data-src") or ""
        m = re.search(r'url\(["\']?([^"\')]+)', tag.get("style", ""))
        src = src or (m.group(1) if m else "")
        if src and re.search(r"\.(jpg|png|webp)", src, re.I):
            print(urljoin(d, src))   # 原图直链；要落盘就再 requests 二进制写 downloads/
```

要点：
- `data-src` 惰性加载属性比 `src` 更常见，两个都试
- `background-image:url(...)` 要正则从 style 里抠
- 翻页：在**同一个脚本里**找「下一页」链接循环，别分段调用
- 下载落盘写到 `PROJECT_ROOT / "downloads" / <站点名>/`（system.md 下载路径硬规则）

## 特殊站型

- **wallhaven.cc**：有公开 JSON API `https://wallhaven.cc/api/v1/search?q=<kw>&page=<n>`（免 key），脚本首选这条路，别解析 HTML
- **WallpaperHub / 极简壁纸** 类：SPA 站，S1 拿不到 → `browser_render` + `wait_selector` 等卡片出现
- **动态壁纸站（steam workshop 风格）**：本质是 zip 包下载，直链可能带 token 时效 —— 当场下完

## 收尾

首次成功抓某个壁纸站后，问用户是否 `save_site_profile(strategy="wallpaper", notes=站点要点)` 存档 —— 下次同站直接按档案走。
