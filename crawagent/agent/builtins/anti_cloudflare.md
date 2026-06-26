# Skill: 反爬页面绕过

name: anti_cloudflare_bypass
description: 适合有 Cloudflare/反爬检测的站点，使用浏览器自动化绕过
trigger_keywords: [cloudflare, 反爬, 验证码, 人机验证, 403]
examples:
  - "这个网站有 Cloudflare 验证，帮我绕过"
  - "爬取被反爬拦截的页面"

prompt: |
  当用户请求有反爬检测的网站时：
  1. 优先使用 browser_crawler 工具
  2. 设置 wait_seconds 参数等待 JS 执行完成
  3. 如果仍有验证码，提示用户手动处理（debug_mode=True）
  4. 输出格式：页面 HTML 或提取的结构化数据

tags: [反爬, 浏览器, 绕过]
version: 1.0