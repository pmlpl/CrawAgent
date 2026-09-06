# 0002 — 翻页通用化 v1 只做静态站；深度爬取不通用化

P2-3 的两个诉求里，翻页是机械工作值得内置；深度爬取（进详情页二次提取）是语义工作，run_custom_script + BATCH-FIRST 已验证可行。决定：新增共享翻页模块与 `extract_list_paged` 工具，v1 只支持静态分页——URL 参数猜测（page/p/pageNum）+ 下一页链接追踪（rel=next / class 含 next / 「下一页」文案）；JS 渲染分页（加载更多/无限滚动）明确出局，继续走 browse_and_crawl / playwright MCP / run_custom_script。

## Considered Options

- 给 extract_list 加翻页参数：破坏其「纯解析器：收 html 不收 url」的干净契约
- 不写代码只强化 batch-crawl-playbook：静态站翻页是确定性劳动，不值得每次烧 token 写脚本

## Consequences

- extract_list_paged 对 JS 分页站点翻完静态能翻的就停——这是设计行为不是 bug；system.md 引导切脚本路径
- wallpaper_tool 的 _auto_paginate 收编进共享模块，行为为原实现的超集（多了下一页链接追踪）
