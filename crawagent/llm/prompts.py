"""CrawAgent 提示词工程（P2-5）

6 个高质量提示词 + JSON_SCHEMA_BUILDER：
1. PROMPT_FILTER_CONTENT   — LLM 内容过滤（内容清洗）
2. PROMPT_EXTRACT_BLOCKS   — 语义分块提取（LLM 提取）
3. PROMPT_EXTRACT_JSON     — 结构化 JSON 提取（LLM 提取）
4. PROMPT_HTML_TO_MARKDOWN — HTML → 干净 Markdown（内容清洗）
5. PROMPT_CHUNK_SUMMARY    — 分块摘要（上下文压缩/摘要过滤）
6. PROMPT_JSON_SCHEMA      — 从自然语言需求生成 JSON Schema

文件末尾保留旧流水线提示词（CLASSIFY/PLAN/DECIDE 等），保证 graph 兼容。
"""

# ==========================================================================
# P2-5-1: LLM 内容过滤
# ==========================================================================

PROMPT_FILTER_CONTENT = """你是网页内容过滤器。给定一段网页正文文本（按块切分），
过滤掉与页面主题无关的噪声内容（导航、广告、推荐、评论、版权声明、登录引导等）。

页面主题（title/keywords/description，可能为空）：
{query}

文本块：
{content}

输出严格 JSON，不要任何解释：
{{
  "kept": [
    {{"index": 0, "reason": "保留理由（一句话）"}}
  ]
}}

规则：
1. 只输出 JSON，key 顺序保持原样
2. 与主题相关的正文、标题、表格、代码块必须保留
3. 与主题无关的导航/广告/推荐/评论必须丢弃
4. 拿不准时倾向保留（误删正文比保留噪声更糟）
"""


# ==========================================================================
# P2-5-2: 语义分块提取
# ==========================================================================

PROMPT_EXTRACT_BLOCKS = """你的任务是把下面网页内容切分为语义相关的块，并为每个块生成 JSON。

网页 URL：
<url>{url}</url>

网页内容：
<html>
{html}
</html>

每个块包含：
- index: 块在内容中的顺序（整数）
- tags: 描述该块主题的标签（字符串数组，1-3 个）
- content: 该块的文本内容（字符串数组，保持原文，仅做必要清洗）

输出格式（放在 <blocks> 标签内，必须是可解析的 JSON 数组）：
<blocks>
[
  {{
    "index": 0,
    "tags": ["introduction"],
    "content": ["第一段正文..."]
  }}
]
</blocks>

规则：
1. 按原文顺序输出，块之间不重不漏
2. 内容只做空白压缩，不要改写、不要翻译
3. 跳过纯导航/广告/脚本片段
4. 只输出 <blocks> 包裹的 JSON，不要任何解释
"""


# ==========================================================================
# P2-5-3: 结构化 JSON 提取
# ==========================================================================

PROMPT_EXTRACT_JSON = """从下面的网页内容中提取结构化数据，严格按目标 Schema 输出。

目标 URL：{url}

目标 Schema（JSON Schema）：
{schema}

网页内容：
<html>
{html}
</html>

输出严格 JSON（一个对象数组），不要任何解释：
[
  {{
    "field1": "value1"
  }}
]

规则：
1. 只输出 JSON 数组，数组内每个对象只包含 Schema 定义的字段
2. 字段找不到时填 null（或按 Schema 要求），不要编造数据
3. 列表页提取多条，详情页提取 1 条
4. 链接字段（url/image）必须补全为绝对地址
5. 数字字段只保留数字（价格去掉货币符号），文本字段压缩空白
"""


# ==========================================================================
# P2-5-4: HTML → 干净 Markdown
# ==========================================================================

PROMPT_HTML_TO_MARKDOWN = """把下面的 HTML 转为干净、可读的 Markdown 文档。

页面标题：{title}
页面 URL：{url}

HTML 内容：
<html>
{html}
</html>

输出要求：
1. 只输出 Markdown，不要任何解释或代码围栏包裹
2. 保留标题层级（# ## ###）、列表、表格、代码块、引用、链接
3. 链接保留原始 URL：如 [文本](https://...)
4. 删除导航、广告、推荐、评论等噪声，正文一句不少
5. 图片保留为 ![alt](src) 形式
6. 正文段落之间用空行分隔
"""


# ==========================================================================
# P2-5-5: 分块摘要
# ==========================================================================

PROMPT_CHUNK_SUMMARY = """压缩下面这段对话/文本为简洁摘要，保留关键信息。

<content>
{content}
</content>

要求：
1. 摘要不超过原文的 1/4，用中文输出
2. 必须保留：关键结论、数字、价格、URL、时间、人物/实体名称
3. 按原顺序组织，不要添加原文没有的信息
4. 只输出摘要正文，不要前缀"摘要："
"""


# ==========================================================================
# P2-5-6: 从自然语言生成 JSON Schema
# ==========================================================================

PROMPT_JSON_SCHEMA = """你是 JSON Schema 设计师。根据用户对目标字段的描述，
生成严格的 JSON Schema。

用户需求：{request}

输出严格 JSON（只输出 JSON，不要任何解释）：
{{
  "type": "object",
  "properties": {{
    "field_name": {{
      "type": "string|integer|number|boolean|array|object",
      "description": "字段含义"
    }}
  }},
  "required": ["必填字段名"],
  "additionalProperties": false
}}

规则：
1. 字段名用 snake_case
2. 金额/评分用 number，数量用 integer，是否用 boolean
3. 列表类字段 type=array，并给出 items
4. required 只放页面中必定存在的字段
"""


# ==========================================================================
# JSON_SCHEMA_BUILDER：规则化生成（不依赖 LLM，供 P2 各模块复用）
# ==========================================================================

_SCHEMA_TYPE_MAP = {
    "str": "string",
    "string": "string",
    "text": "string",
    "int": "integer",
    "integer": "integer",
    "float": "number",
    "number": "number",
    "double": "number",
    "bool": "boolean",
    "boolean": "boolean",
    "array": "array",
    "list": "array",
    "object": "object",
    "optional_str": "string",
    "optional_string": "string",
    "optional_int": "integer",
    "optional_float": "number",
    "optional_bool": "boolean",
}


def build_json_schema(fields: dict, title: str = "ExtractedItem") -> dict:
    """根据字段定义构建 JSON Schema。

    fields 支持两种格式：
    - {"title": "标题"}                          # 简写：类型默认为 str
    - {"price": {"type": "float", "description": "价格", "required": True}}

    Returns:
        JSON Schema dict（可与 pydantic 的 model_json_schema 互转）。
    """
    properties: dict = {}
    required: list = []

    for name, spec in fields.items():
        if isinstance(spec, str):
            type_str, desc = spec, ""
        elif isinstance(spec, dict):
            type_str = str(spec.get("type", "str"))
            desc = str(spec.get("description", ""))
        else:
            type_str, desc = "str", str(spec)

        optional = type_str.startswith("optional")
        base_type = _SCHEMA_TYPE_MAP.get(type_str, "string")

        prop: dict = {"type": base_type, "description": desc}
        if base_type == "array":
            prop["items"] = spec.get("items", {"type": "string"}) if isinstance(spec, dict) else {"type": "string"}

        properties[name] = prop
        if not optional:
            required.append(name)

    return {
        "type": "object",
        "title": title,
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def pydantic_to_json_schema(model_cls) -> dict:
    """Pydantic 模型 → JSON Schema dict。"""
    schema = model_cls.model_json_schema()
    # 精简为与 build_json_schema 一致的结构
    return {
        "type": "object",
        "title": schema.get("title", model_cls.__name__),
        "properties": schema.get("properties", {}),
        "required": schema.get("required", []),
        "additionalProperties": False,
    }


# ==========================================================================
# 旧流水线提示词（兼容保留，供 graph/agent_workflow 使用）
# ==========================================================================

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
