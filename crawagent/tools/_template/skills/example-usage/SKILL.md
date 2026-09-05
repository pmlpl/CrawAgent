---
name: example-usage
description: 示例技能：演示插件如何自带 SKILL.md。安装本插件后，Agent 处理 RSS/播客/博客内容发现类任务时会读到这份攻略。
---

# RSS 内容发现攻略

本技能由插件自带（`plugins/<插件名>/skills/<技能名>/SKILL.md`），安装插件后自动进
Agent 的技能索引，无需改任何配置。

## 什么时候用 RSS

用户要「订阅/追踪/发现某站点的新内容」而站点提供 RSS 时，RSS 永远是首选数据源：
一次请求拿全结构化字段，无反爬，无翻页。

- 探测 feed：站点根或文章页找 `<link rel="alternate" type="application/rss+xml">`，
  常见路径 `/rss` `/feed` `/atom.xml` `/rss.xml` `/index.xml`
- 拿到 feed URL 后调 `fetch_rss_feed(feed_url, max_items=20)`
- 需要全文的条目 → 对条目链接走常规抓取流程（crawl_webpage → extract_content）

## 已知模式

- Hexo/Hugo/Jekyll 静态博客：`/atom.xml` 或 `/index.xml`
- WordPress：`/feed`；Typecho：`/feed/`
- 播客：feed 里的 `enclosure` 字段就是音频直链，可直接 download
