# Skill: 数据库保存

name: database_save
description: 将爬取的数据保存到 SQLite/MySQL 数据库，支持结构化存储和查询
trigger_keywords: [保存数据库, 保存到数据库, 保存到db, 保存到mysql, 存到数据库, 入库, 入库保存, 数据库, db, sqlite]
examples:
  - "帮我抓取并保存到数据库"
  - "爬取这个网站的数据存到数据库"
  - "把内容保存到数据库"

prompt: |
  当用户要求将爬取的数据保存到数据库时，使用 database_save 工具。

  ## 数据库配置

  默认使用 SQLite 数据库：
  - 数据库文件：output/crawls.db
  - 如需 MySQL，可在调用时指定参数

  ## 数据库字段

  数据库表（crawl_records）包含以下字段：
  - url: 网页链接（唯一索引）
  - title: 标题
  - content: 内容
  - source: 来源网站（从 URL 自动提取，如 zhihu、weibo）
  - category: 分类
  - author: 作者
  - published_date: 发布时间
  - created_at: 入库时间（自动生成）
  - extra_data: 其他额外数据（JSON 格式）

  ## 保存策略

  1. 爬取数据后，调用 database_save 保存
  2. 如果 URL 已存在，自动更新（不去重）
  3. 可以批量保存多条记录

  ## 使用示例

  ```python
  from crawagent.tools.database import save_to_db, get_default_db

  # 方式1：保存单条
  save_to_db(
      url="https://example.com/article/1",
      title="文章标题",
      content="文章内容...",
      source="example",
      author="作者名",
      published_date="2024-01-01"
  )

  # 方式2：批量保存
  db = get_default_db()
  db.save_batch([
      {"url": "url1", "title": "标题1", "content": "内容1"},
      {"url": "url2", "title": "标题2", "content": "内容2"},
  ])

  # 查询数据
  records = db.query(source="zhihu", limit=10)

  # 导出数据
  db.export_to_json("output/zhihu_data.json")
  db.export_to_csv("output/zhihu_data.csv")
  ```

  ## 工作流程

  1. 用户要求保存到数据库
  2. 爬取网页内容
  3. 从内容中提取结构化字段（标题、作者、时间等）
  4. 调用 database_save 保存到数据库
  5. 返回保存结果（记录数、数据库路径等）

tags: [数据库, SQLite, MySQL, 保存, 存储, 数据持久化]
version: 1.0
