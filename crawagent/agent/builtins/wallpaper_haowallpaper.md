# Skill: 壁纸批量下载

name: wallpaper_batch_download
description: 适合壁纸网站（如 haowallpaper.com），批量提取并下载高质量壁纸
trigger_keywords: [壁纸, wallpaper, haowallpaper, 批量下载图片]
examples:
  - "帮我下载这个壁纸网站的所有壁纸"
  - "爬取 haowallpaper 的最新壁纸"

prompt: |
  当用户请求壁纸下载任务时：
  1. 使用 wallpaper_scraper 工具
  2. 自动进入详情页提取主壁纸/视频壁纸
  3. 支持设置 max_count 参数限制下载数量
  4. 输出格式：JSON 数组含文件名/URL/保存路径

tags: [壁纸, 图片, 批量, 下载]
version: 1.0