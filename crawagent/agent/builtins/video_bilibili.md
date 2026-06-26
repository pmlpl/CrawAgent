# Skill: B站视频批量抓取

name: bilibili_batch_scrape
description: 适合B站视频/番剧页，提取视频流地址和元数据
trigger_keywords: [bilibili, B站, b站, 批量下载视频, 番剧]
examples:
  - "帮我下载B站的这个视频"
  - "爬取B站这个番剧的所有剧集"

prompt: |
  当用户请求B站相关任务时：
  1. 首先判断是单视频还是番剧/合集页
  2. 单视频"仅抓取页面信息" → 使用 basic_crawler（快速、稳定）
  3. 单视频"下载视频/抓流地址" → 使用 browser_crawler（packet_crawler 当前有 bug）
  4. 番剧页 → 提取所有剧集链接，逐个用 basic_crawler 抓取
  5. B站页面用 basic_crawler 已能拿到标题、播放量、简介等主要信息
  6. 输出格式：JSON 数组含标题/URL/下载状态

tags: [视频, B站, 批量, 抓包]
version: 1.1