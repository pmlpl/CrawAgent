# Skill: 优酷视频批量抓取

name: youku_batch_scrape
description: 适合优酷专辑页，批量提取剧集并下载
trigger_keywords: [优酷, youku, 批量下载视频, 剧集]
examples:
  - "帮我下载优酷的这个视频"
  - "爬取优酷这个专辑的所有剧集"

prompt: |
  当用户请求优酷相关任务时：
  1. 首先判断是单视频还是专辑页
  2. 单视频 → 使用 packet_crawler 抓取流地址
  3. 专辑页 → 使用 crawl_show_preview 提取所有剧集
  4. 每个视频调用 ffmpeg 转换格式
  5. 输出格式：JSON 数组含标题/URL/下载状态

tags: [视频, 优酷, 批量, 抓包]
version: 1.0