CLASSIFY_PROMPT = """你是网页分类器。根据用户指令和种子 URL，判断任务类型。

用户指令: {user_input}
种子 URL: {seed_urls}

输出 JSON:
{{
  "page_type": "video_list|article|search_result|product_list|unknown",
  "intent": "crawl|analyze|chat",
  "target_fields": ["title", "url", "content", "..."]
}}

规则：
- 如果指令包含"爬取/抓取/采集/爬/下载" -> intent="crawl"
- 如果指令包含"分析/看看/是什么" -> intent="analyze"
- 否则 intent="chat"
- 根据 URL 域名/路径判断 page_type"""

SITE_ANALYZER_PROMPT = """你是网页结构分析专家。给定页面 URL、标题、DOM 片段和网络请求摘要，输出严格 JSON。

输入：
- URL: {url}
- 标题: {title}
- DOM 片段（截断）:
{dom_snippet}
- 网络请求摘要（前30个）:
{network_summary}

任务：分析页面结构，输出以下字段的 JSON：

{{
  "page_type": "video_list|article|search_result|product_list|unknown",
  "data_source": "dom|xhr_json|graphql|api",
  "selectors": {{
    "list_container": "列表容器 CSS 选择器",
    "item": "单个条目 CSS 选择器",
    "title": "标题选择器（相对于 item）",
    "url": "链接选择器（相对于 item，通常是 a@href）",
    "extra": {{
      "thumbnail": "缩略图选择器",
      "duration": "时长选择器",
      "author": "作者选择器"
    }}
  }},
  "api_endpoint": "若数据来自 XHR/JSON，给出接口 URL",
  "api_method": "GET|POST",
  "api_headers": {{}},
  "api_params": {{}},
  "pagination": {{
    "type": "page_param|cursor|offset|infinite_scroll|none",
    "param": "分页参数名（如 page、cursor、offset）",
    "max_page_selector": "最大页码选择器",
    "cursor_selector": "游标选择器",
    "has_next_selector": "是否有下一页选择器"
  }},
  "anti_bot_signs": ["cloudflare", "slider_captcha", "recaptcha", "waf", "rate_limit", "none"],
  "confidence": 0.0-1.0
}}

规则：
1. 优先判断数据源：看网络请求中是否有返回列表数据的 JSON 接口
2. 选择器要具体、稳定，避免使用 nth-child 等脆弱选择器
3. 如果是视频列表页，extra 必须包含 thumbnail、duration
4. anti_bot_signs 从响应头、网络错误、页面特征判断
5. confidence 反映你对分析的把握程度
6. 只输出 JSON，不要任何解释"""

PLAN_PROMPT = """你是爬虫规划师。根据用户需求、页面类型、种子 URL 和目标字段，制定爬取计划。

用户需求: {user_input}
页面类型: {page_type}
种子 URL: {seed_urls}
目标字段: {target_fields}

输出 JSON:
{{
  "seed_urls": [...],
  "target_schema": {{
    "fields": {{
      "field_name": {{"type": "str|int|float|bool", "description": "描述", "required": true}}
    }}
  }},
  "max_pages": 50,
  "max_depth": 2,
  "per_domain_rate": 0.5,
  "require_login": false,
  "cookies": {{}},
  "user_instructions": "原始用户指令"
}}

规则：
- 视频列表页 -> 字段包含 title, url, thumbnail, duration, video_url
- 文章列表 -> title, url, summary, author, publish_time
- 搜索结果 -> title, url, snippet
- 商品列表 -> title, url, price, image, rating
- max_pages 默认 50，用户说"全部"可设 1000
- per_domain_rate 默认 0.5（每秒 0.5 请求）"""

DECIDE_PROMPT = """你是爬虫决策引擎。根据当前爬取状态，决定下一步动作。

当前状态:
- 已爬取页数: {pages_crawled}
- 最大页数: {max_pages}
- 已提取条目: {items_extracted}
- 错误数: {errors}
- 最近错误: {last_error}
- 反爬检测: {anti_bot_detected}

输出 JSON:
{{
  "action": "continue|pause|anti_bot|human|done",
  "reason": "决策理由"
}}

规则：
- pages_crawled >= max_pages -> done
- errors > 10 且最近持续报错 -> pause
- anti_bot_detected -> anti_bot
- 需要登录/验证码且无法自动处理 -> human
- 正常且未达上限 -> continue"""

EXTRACTION_SCHEMA_PROMPT = """根据目标字段描述，生成 Pydantic 模型定义。

目标字段: {target_fields}

输出 JSON Schema:
{{
  "fields": {{
    "field_name": {{"type": "str|int|float|bool|optional_str", "description": "描述", "required": true}}
  }}
}}

类型映射：
- 文本/标题/链接 -> str
- 数字/价格/时长 -> int/float
- 是否/标志 -> bool
- 可能缺失 -> optional_str"""