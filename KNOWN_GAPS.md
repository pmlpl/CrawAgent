# 已知缺口清单

> 走完全部开发流程（P0–P8）后再统一补充修复。
> 新发现的缺口追加到末尾，标注发现阶段、归属阶段、影响范围。
> 
> **更新**：2026-08-01 — GAP-002 已在 P7 修复
> **更新**：2026-08-02 — 新增 GAP-004 ~ GAP-011（图片抓取优化与图片站训练阶段发现，GAP-006/007/008/009/010/011 已修复）

---

## GAP-001：html2text 处理 HN 链接 baseurl 拼接错误

- **发现阶段**：P2 验收
- **归属阶段**：P2（内容清洗）
- **影响范围**：Markdown 生成
- **现象**：html2text 处理 Hacker News 端到端 Markdown 链接时，相对链接拼接错误，生成如 `https://news.ycombinator.com/</item/1>` 的非法 URL
- **根因**：html2text 的 baseurl 参数与相对路径 `<a href="/item/1">` 拼接逻辑异常
- **影响**：不影响 P2 验收（非硬性要求），但生成的 Markdown 链接不可点击
- **建议修复**：在 markdown_generator 中对生成的 URL 做后处理校验，或换用 markdownify

## GAP-002：MediaDownloader 扩展名推断不读 Content-Type ✅ 已修复

- **发现阶段**：P3 收尾（图片下载集成）
- **归属阶段**：P7（影视内容 + 会员，P7-4 明确涉及 media_downloader）
- **影响范围**：图片下载文件名扩展名
- **现象**：当图片 URL 无扩展名（如 `https://picsum.photos/200/100`）时，下载文件扩展名为 `.bin`
- **根因**：[_download_httpx](crawagent/output/media_downloader.py) 仅从 URL 路径推断扩展名，未读 `Content-Type` 响应头
- **影响**：Apple 等站点图片 URL 通常含 `.jpg/.png`，不受影响；但部分 CDN 图片会生成 `.bin` 文件
- **建议修复**：在 `_download_httpx` 中读取 `resp.headers.get("Content-Type")`，映射 `image/jpeg→.jpg`、`image/png→.png` 等
- **修复状态**：✅ 已在 P7 修复（2026-08-01）— 新增 `_infer_ext_from_content_type()` 方法 + `_CONTENT_TYPE_EXT_MAP` 常量（36 种 MIME 类型映射），URL 无扩展名时发 HEAD 请求读 Content-Type 推断

## GAP-003：save_executor 图片下载为串行

- **发现阶段**：P3 收尾（图片下载集成）
- **归属阶段**：P6（Compaction + Durability + 深度爬取，性能优化）
- **影响范围**：大批量图片下载耗时
- **现象**：[_maybe_download_images](crawagent/harness/tools.py) 当前串行下载，每张图片一次 httpx 请求
- **根因**：为避免对目标站压力过大刻意串行
- **影响**：单页 3-5 张图片无感知；批量抓取几十张图片时耗时会增加
- **建议修复**：引入并发度限制（如 `asyncio.Semaphore(5)`）+ 可配置并发数

## GAP-004：SPA 客户端路由依赖框架标记检测，部分站点无法识别

- **发现阶段**：P2+（图片抓取优化，2026-08-02）
- **归属阶段**：P2+（持续优化）
- **影响范围**：SPA 站点子路由抓取
- **现象**：haowallpaper.com 子分类页（/wallpaper、/fengjing）服务器端返回 404，需检测 `__NUXT__`/`_nuxt` 等框架标记后才触发客户端路由等待
- **根因**：当前 SPA 检测依赖已知框架标记字符串；若站点用自定义构建（无标准标记）或标记被混淆，4xx 会被误判为真正失败
- **影响**：已覆盖 Nuxt/Vue/React/Next/Angular 主流框架；自定义 SPA 构建仍可能失败
- **建议修复**：将 SPA 检测结果写入网站画像（`site_profile.py` 的 `spa_type`/`uses_client_routing` 字段），下次爬取同域名时直接从画像读取，无需再次检测

## GAP-005：图片下载失败无重试与降级策略

- **发现阶段**：P2+（图片抓取优化，2026-08-02）
- **归属阶段**：P2+（持续优化）
- **影响范围**：图片下载完整性
- **现象**：[_maybe_download_images](crawagent/harness/tools.py) 单次 httpx 下载失败即标记 failed，无重试；部分图片 URL 带防盗链 Referer 校验
- **根因**：未针对图片下载做重试/防盗链头处理
- **影响**：个别图片下载失败不影响整体流程（已有 failed 计数），但完整度下降
- **建议修复**：下载失败重试 1-2 次；对 403 响应尝试带 `Referer` 请求头重试；失败图片保留原始 URL 以便后续补下
- **修复状态**：✅ 已部分修复（2026-08-02，3.4 重试队列/断点续抓）— `_maybe_download_images` 下载失败自动重试 3 次（0.5s/1s 退避）；403 防盗链 `Referer` 重试与失败 URL 补下保留待后续

## GAP-006：图片提取未过滤导航/装饰性小图

- **发现阶段**：P2+（图片抓取优化，2026-08-02）
- **归属阶段**：P2+（持续优化）
- **影响范围**：图片提取质量
- **现象**：`extract_images()` 按最小宽高（50×50）过滤，但导航 logo、图标、背景装饰图仍可能混入结果
- **根因**：仅依赖宽高过滤，未结合 DOM 上下文（nav/header/footer/icon 类）判断
- **影响**：图片列表含少量非目标图，影响质量评分
- **修复状态**：✅ 已修复（2026-08-02，图片站训练）— 新增 `_is_ui_noise_image()`（过滤 favicon/pwa/logo/icon/avatar/defaultBg 等 URL 特征 + "上传图片/用户头像"等 UI alt 文案 + `/_nuxt/` 构建产物）+ `_content_image_score()`（壁纸/高清/封面等 alt 与 URL 特征 → 排序优先）；`extract_images` 排序改为 内容评分 > kind 优先级 > 尺寸
- **建议修复**（残留）：结合网站画像的 `nav_links` 排除导航区图片（当前 URL/alt 特征已覆盖大多数场景）

## GAP-008：PruningContentFilter 误删媒体节点（img），图片站 clean 后无图

- **发现阶段**：P2+（图片站训练，2026-08-02）
- **归属阶段**：P2+（持续优化）
- **影响范围**：图片站端到端爬取（clean → extract 链路）
- **现象**：haowallpaper.com 浏览器模式抓取 12 张 `<img>`，clean 后仅剩 0 张 → `extract_images` 提取 0 张 → supervisor NEED_REANALYSIS 410
- **根因**：[PruningContentFilter.filter_content](crawagent/core/content_filter.py) 步骤 2"低内容节点"把无文本且无后代的叶子容器 `decompose()`，而 `<img>` 自身无文本 → 被误当低内容节点删除（`el.find(["img", ...])` 只判断后代，img 无后代）
- **影响**：所有图片站的 clean 阶段都会丢图，图片提取链路隐性失效（单独调 extract_images 用原始 HTML 正常，supervisor 用 clean_html 失败）
- **修复状态**：✅ 已修复（2026-08-02）— 步骤 2 增加 `MEDIA_TAGS`（img/picture/source/video/audio/table/pre/code/canvas）白名单，媒体节点本体跳过删除
- **测试**：`test_prune_keeps_media_nodes`（img 保留回归）+ `test_extract_images_filters_ui_noise`/`test_extract_images_content_images_first`/`test_parse_srcset_skips_data_uri`/`test_is_ui_noise_image_rules`（图片提取加强）

## GAP-009：data URI 内嵌图被 srcset 切分误拼为伪 URL

- **发现阶段**：P2+（图片站训练，2026-08-02）
- **归属阶段**：P2+（持续优化）
- **影响范围**：图片提取 URL 质量
- **现象**：`extract_images` 结果出现 `https://haowallpaper.com/iVBORw0KGgo...`（base64 data URI 被当相对路径拼接）
- **根因**：`_parse_srcset` 用逗号切分 srcset，data URI（`data:image/png;base64,xxx`）内嵌逗号被切成碎片，非 `data:` 前缀的 base64 片段被当作 URL
- **影响**：图片列表含无法下载的伪 URL
- **修复状态**：✅ 已修复（2026-08-02）— `_parse_srcset` 开头检测 `data:` / `;base64,` 前缀整体返回空；`_push` 已有 `data:` 前缀过滤兜底

## GAP-007：SPA 壳页判定误用原始 HTML 文本长度，浏览器自动升级兜底失效

- **发现阶段**：P2+（爬虫能力加强，2026-08-02）
- **归属阶段**：P2+（持续优化）
- **影响范围**：HTTP 模式自动升级浏览器的兜底判定
- **现象**：c12（AES 反爬挑战页）HTTP 模式提取 0 条后，兜底 `_looks_spa` 判定为 False，未触发 `use_browser=True` 重抓，直接返回 410；或 LLM 兜底从壳页提取 1 条垃圾数据（confidence=75 刚过阈值）被直接保存
- **根因**：
  1. `_looks_spa` 用原始 HTML 的 `text_content()` 估算正文长度，但原始 HTML 混入导航/页脚/内嵌 JSON（剔除 script 后 c12 仍有 3711 字符），而 clean 后 markdown 仅 222 字符 → 误判
  2. LLM 兜底提取少量条目（<5）时 confidence 计算（50 + 条数×5 + 蓝图命中 20）恰好过 70 阈值，劣质结果被当成功保存
- **影响**：SPA/懒加载站点 HTTP 模式无法自动升级浏览器，产出 0 条或 1 条垃圾数据
- **修复状态**：✅ 已修复（2026-08-02）— `_looks_spa` 改用 clean 后 markdown 长度判断；新增 `_count_too_low`（提取 1~4 条且页面含 `<table>` 或链接 ≥20 → 视为列表页提取不完整，触发浏览器升级）；save 后回填 `result["items"]`/`count` 供 API 消费
- **残留**：c13 数据行需 JS 逆向（分块传输 + 签名 API），浏览器渲染后表格仍只有表头，属专项开发边界

## GAP-010：登录类站点（抖音等）无登录态方案，抓取被风控拦截 ✅ 已修复

- **发现阶段**：P2+（短视频平台训练，2026-08-02）
- **归属阶段**：P2+（持续优化）
- **影响范围**：抖音/小红书等需登录才能完整抓取的站点
- **现象**：抖音无登录访问受风控硬限制（`_$jsvmprt` 加密 + 验证码 iframe + play_addr 签名 + 播放链接 24h 有效期），HTTP 模式拿到的是 72KB 风控壳页（无视频数据）；yt-dlp 直接提取报 `Fresh cookies (not necessarily logged in) are needed`
- **根因**：站点要求浏览器指纹 + 匿名 cookie（ttwid 等）才下发真实媒体地址；纯 HTTP 无 JS 渲染拿不到完整 cookie 集
- **影响**：登录类站点抓取产出 0 条或仅标题
- **修复状态**：✅ 已修复（2026-08-02）—
  1. [profile_manager.py](crawagent/sessions/profile_manager.py) 新增 `grab_anonymous_cookies()`：**Playwright 无头浏览器渲染一次自动收集匿名 cookie（ttwid/odin_tt/passport_csrf_token 等 30+ 个），无需账号登录**，保存 cookies.txt（Netscape 格式）；`login_interactive`（用户手动登录）保留用于私密/好友可见视频
  2. [media_downloader.py](crawagent/output/media_downloader.py) 下载抖音视频时自动兜底：无 cookies_file 时调用 `grab_anonymous_cookies` 拿匿名 cookie 再走 yt-dlp（`_needs_anonymous_cookie` 判定 + `_auto_anonymous_cookies` 降级）
  3. [server.py](crawagent/api/server.py) P8 登录态 API 新增 `POST /api/auth/profile/anonymous`（自动匿名 cookie）；`POST /api/video/download` 直接传抖音 URL 即可全自动下载
- **实测验证**（2026-08-02）：匿名 cookie（38 个）+ yt-dlp 提取抖音视频全部清晰度格式（最高 1280x720 h265 直链），47.2 MiB MP4 下载成功且 ftyp 魔数有效——**公开视频无需登录**；私密/好友可见视频才需要 `login_interactive` 手动登录
- **测试**：`test_profile_manager.py` 6 项（Netscape 格式转换、去重、转义、缺 storage 报错、has_profile 判定、MediaDownloader 风控站判定）；全量 137 项通过

## GAP-011：API 型强风控站点（JS 签名）无法从壳页提取数据 ✅ 已修复

- **发现阶段**：P9（强风控 / JS 逆向训练，2026-08-02）
- **归属阶段**：P9（持续优化）
- **影响范围**：抖音 / 小红书等前端调用签名 API 的站点（a_bogus / x-s 等签名参数由 JS 运行时计算）
- **现象**：这类站点 HTML 是壳页（数据在签名 API 响应里），常规 crawl/extract 拿不到数据；人工逆向签名算法成本高且随时失效
- **根因**：数据链路为 `JS 计算签名 → 请求带签名 API → 返回 JSON`；HTTP 抓壳页无数据，伪造签名需逆向算法
- **影响**：API 型强风控站点抓取产出 0 条
- **修复状态**：✅ 已修复（2026-08-02）— 新增 [api_harvester.py](crawagent/core/api_harvester.py)（`BrowserAPIHarvester`）：
  1. **浏览器即签名机**：Playwright 打开页面执行站点 JS → 签名自动计算 → 拦截 `page.on("response")` 拿带签名 API 响应，**无需逆向任何签名算法**
  2. 滚动触发分页懒加载；启发式提取列表数据（容器键/媒体字段/内容字段加权 + 配置类过滤）与媒体直链（扩展名 / 字段名 / 媒体 CDN 域名识别）
  3. 风控特征检测（验证码/验证/risk）；收集页面 `<video>` 标签 src
  4. [tools.py](crawagent/harness/tools.py) 新增 `harvest_api` 工具（executor 注册），Agent 一键调用
- **实测验证**（2026-08-02）：harvest 抖音首页 → 捕获 **85~100 条签名 API**（`/aweme/v1/web/` 系列）+ **20 条 douyinvod 签名视频直链**；带 `Referer` 直链下载 HTTP 200、`video/mp4`、ftyp 有效——全程无人工干预
- **测试**：`test_api_harvester.py` 9 项（列表提取权重/配置过滤、媒体扩展名/字段/域名识别、去重）；全量 146 项通过
