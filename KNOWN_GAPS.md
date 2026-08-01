# 已知缺口清单

> 走完全部开发流程（P0–P8）后再统一补充修复。
> 新发现的缺口追加到末尾，标注发现阶段、归属阶段、影响范围。
> 
> **更新**：2026-08-01 — GAP-002 已在 P7 修复

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
