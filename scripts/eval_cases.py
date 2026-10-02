"""040 Agent 行为评测集 — 黄金集 15 例（输入、桩剧本、期望）。

每例锚定 system.md 当前版（038/039 后）的一条硬规则。桩输出全部 canned，
评测过程不发生真实网络请求、不写真实 data/。

剧本约定：
- script: 工具名 -> 返回串列表（按调用次序逐个弹出，只剩最后一个时反复返回它）
- ask:    ask_user 的排队答案（弹尽后默认答「不用」）
- 期望词表见 eval_behavior.py（expect_seq / forbid / count / total_max /
  arg_contains / reply_re / check）

注意：本文件源码不出现尖括号字符——Write/Edit 工具会吞尖括号（项目已知坑），
HTML 桩一律用 chr(60)/chr(62) 拼接。
"""
from dataclasses import dataclass, field
from typing import Callable

LT, GT = chr(60), chr(62)


def _el(name: str, inner: str = "") -> str:
    """拼一个 HTML 元素（避开源码尖括号）；!开头的声明型标签不补闭合。"""
    if name.startswith("!"):
        return f"{LT}{name}{GT}"
    return f"{LT}{name}{GT}{inner}{LT}/{name}{GT}"


# ---------------------------------------------------------------------------
# 通用桩素材
# ---------------------------------------------------------------------------

KB_MISS = (
    "FTS5 检索完成：无命中记录。知识库中暂无与该主题相关的档案（eval stub），"
    "可走正常抓取流程。"
)

KB_HIT = (
    "[KB-HIT] 命中本会话档案 1 条（eval stub）：\n"
    "标题：blog.example.com 第 123 篇文章全文\n"
    "摘要：文章系统介绍了观测方法，结尾提到「星桥计划的第三次修订完成于 "
    "2031 年 4 月」。\n（内容完整覆盖抓取需求，可直接据此作答，无需重爬。）"
)

HTML_POST = (
    _el("!DOCTYPE html")
    + _el("html",
          _el("head", _el("title", "Example Blog Post"))
          + _el("body",
                _el("article",
                    _el("h1", "城市夜间的光污染观测指南")
                    + _el("p", "本文完整讲述光污染观测的入门方法、器材选择与拍摄参数，"
                              "正文约 3000 字，内容完整无乱码（eval stub）。")))
          ))

HTML_CHAPTER_LIST = (
    _el("!DOCTYPE html")
    + _el("html",
          _el("head", _el("title", "小说《测评员手记》目录"))
          + _el("body",
                _el("div", "共 30 章："
                    + "".join(_el("a", f"第 {i} 章 测评第 {i} 号产品")
                              for i in range(1, 31)))))
    )

EXTRACT_OK = (
    "# 城市夜间的光污染观测指南\n\n本文完整讲述光污染观测的入门方法、"
    "器材选择与拍摄参数（eval stub 正文）。\n\n[CONFIDENCE: score=85]"
)

EXTRACT_LOW = (
    "# 抓取结果\n\n正文过短且含乱码：鈥?潃婢垛…（eval stub 模拟字体加密乱码）\n\n"
    "[LOW CONFIDENCE: score=30, reasons=正文过短; 含乱码字符; 疑似字体加密]"
)

SPA_SHELL = (
    _el("!DOCTYPE html")
    + _el("html",
          _el("head",
              _el("title", "App")
              + f"{LT}script src={chr(34)}/assets/index.9f2c1.js{chr(34)}{GT}{LT}/script{GT}")
          + _el("body", f'{LT}div id={chr(34)}root{chr(34)}{GT}{LT}/div{GT}'
                        f'{LT}script{GT}window.__BOOT__={chr(34)}1{chr(34)}{LT}/script{GT}'))
    # 约 300 字节的 SPA 壳：body 只有一个空挂载点 + 脚本标签，无正文
)

SCRIPT_OK_SIMPLE = (
    "BATCH DONE: 已用 requests 抓取目标页面并解析出正文共 2860 字，"
    "输出如上（eval stub）。"
)

LIST_PAGED_OK = (
    "共抓取 5 页，合并 15 条结果（eval stub）：\n"
    + "\n".join(f"{i}. 新闻条目 {i} — https://news.example.com/p/{i}"
                for i in range(1, 16))
)

WALLPAPER_ZERO = (
    "抓取完成：共 0 个壁纸条目。列表页卡片均为 CSS 背景图渲染，"
    "静态解析未获得任何图片链接（eval stub）。"
)

SCRIPT_WALLPAPER_OK = (
    "BATCH DONE: 用 requests 解析 CSS background-image 后拿到 24 张壁纸直链，"
    "已全部列出（eval stub）。"
)

MCP_UNREACHABLE = (
    "[MCP_UNREACHABLE] anything-analyzer 服务未运行（127.0.0.1:23816 连接被拒）。"
    "请按 ASK-USER 规则用 ask_user 询问用户是否拉起服务"
    "（例如 options=[\"打开 anything-analyzer\", \"暂不打开\"]），"
    "用户同意后才可 check_mcp_status(force=True)。"
)

MCP_ADDED = (
    "[MCP_ADDED] demo 已写入 .env MCP_SERVERS 配置（eval stub，未落盘）。"
    "新工具下轮构建 Agent 时装入，本条消息生效前需新会话轮次。"
)

WEREAD_NO_COOKIE = (
    "[WEREAD_COOKIE_NOT_SET] weread.qq.com 的 Cookie 未配置，无法读取章节列表。"
    "请引导用户提供 Cookie（登录 weread.qq.com → F12 → Cookies → 复制 wr_vid 与 "
    "wr_ssk），拿到后 save_site_profile 存入该站点档案。"
)

CHAPTER_LIST_HTML_NOTE = "（目录页共 30 个章节链接，正文需逐章抓取，eval stub）"


# ---------------------------------------------------------------------------
# 未编排工具的兜底桩（拟真输出，避免「通用 OK」误导模型跑偏）。
# 导出给 eval_behavior.py 使用。
# ---------------------------------------------------------------------------

HTML_GENERIC = (
    _el("!DOCTYPE html")
    + _el("html",
          _el("head", _el("title", "Example Page"))
          + _el("body",
                _el("article",
                    _el("h1", "示例页面标题")
                    + _el("p", "这是目标页面的正文内容：段落完整、无乱码、"
                              "无反爬拦截（eval stub 通用页面）。")))
          ))

EXTRACT_GENERIC = (
    "# 示例页面标题\n\n目标页面的正文内容，段落完整、可直接呈现给用户"
    "（eval stub 通用提取结果）。\n\n[CONFIDENCE: score=82]"
)

LIST_GENERIC = (
    "共抓取 3 页，合并 12 条结果（eval stub 通用输出）：\n"
    + "\n".join(f"{i}. 列表条目 {i} — https://target.example.com/item/{i}"
                for i in range(1, 13))
)

WALLPAPER_GENERIC = (
    "抓取完成：共 8 个壁纸条目（eval stub 通用输出）：\n"
    + "\n".join(f"{i}. 壁纸 {i} STATIC 1920x1080 https://img.example.com/w/{i}.jpg"
                for i in range(1, 9))
)

SCRIPT_GENERIC = (
    "DONE: 脚本执行完成，requests 抓取并解析出目标内容共 1520 字，"
    "结果已打印（eval stub 通用输出）。"
)

SOCIAL_GENERIC = (
    '{"platform": "douyin", "title": "示例视频", "author": "示例作者", '
    '"video_url": "https://v.example.com/v.mp4", "cover": "https://v.example.com/c.jpg"}'
    "\n（eval stub 通用输出）"
)

DEFAULT_STUBS = {
    "crawl_webpage": HTML_GENERIC,
    "browse_and_crawl": HTML_GENERIC,
    "crawl4ai_deep_crawl": "共 6 页 Markdown（eval stub 通用输出）：\n" + LIST_GENERIC,
    "extract_content": EXTRACT_GENERIC,
    "extract_list": "共 12 条（eval stub 通用输出）：\n" + LIST_GENERIC,
    "extract_list_paged": LIST_GENERIC,
    "extract_wallpaper_list": WALLPAPER_GENERIC,
    "wallpaper_detail": WALLPAPER_GENERIC,
    "extract_social_media": SOCIAL_GENERIC,
    "run_custom_script": SCRIPT_GENERIC,
    "list_site_profiles": "NO_PROFILE: 无该站点档案（eval stub）。",
    "recommend_scripts": "NO_SCRIPT: 无匹配的历史脚本模板（eval stub）。",
    "list_crawled_resources": "（空）本会话暂无抓取记录（eval stub）。",
    "list_mcp_servers": "当前配置 0 个 MCP server（eval stub）。",
    "save_record": "SAVED: 已存入知识库（eval stub，未落盘）。",
    "save_to_file": "OK: 已写入 output/eval_stub.md（eval stub，未落盘）。",
    "save_site_profile": "OK: 站点档案已保存（eval stub，未落盘）。",
    "get_proxy": "NO_PROXY",
    "list_proxies": "代理池为空（eval stub）。",
}


# ---------------------------------------------------------------------------
# 用例定义
# ---------------------------------------------------------------------------

@dataclass
class Case:
    id: str
    rule: str                 # 锚定的 system.md 硬规则（报告里显示）
    user: str
    script: dict = field(default_factory=dict)
    ask: list = field(default_factory=list)
    expect_seq: list = field(default_factory=list)   # 工具名有序子序列
    forbid: list = field(default_factory=list)
    count: dict = field(default_factory=dict)        # 工具名 -> {"==":n} / {"<=":n}
    total_max: int | None = None
    arg_contains: list = field(default_factory=list) # (工具名, 参数名, 子串)
    reply_re: str | None = None
    check: Callable | None = None                    # fn(calls, reply) -> 失败理由|None
    timeout: int = 180


def _check_chinese_reply(calls, reply):
    """11 号案例自定义断言：回复汉字数不足 20 视为违反 LANGUAGE RULE。"""
    n = sum(1 for c in reply if chr(0x4E00) <= c <= chr(0x9FFF))
    return None if n >= 20 else f"回复应为中文（汉字数 {n} < 20）"


def _check_no_browse_before_script(calls, reply):
    """03 号案例校准判定：SPA 壳之后、第一个 run_custom_script 之前，
    不许插 browse_and_crawl（硬规则管的是「下一步不许换浏览器工具」，
    脚本成功后再补浏览不算违规——基线实测校准）。"""
    names = [n for n, _ in calls]
    if "crawl_webpage" not in names:
        return "未调用 crawl_webpage"
    i_crawl = names.index("crawl_webpage")
    script_idxs = [i for i, n in enumerate(names) if n == "run_custom_script"]
    if not script_idxs:
        return "SPA 壳后未调用 run_custom_script"
    between = [n for n in names[i_crawl + 1:script_idxs[0]] if n == "browse_and_crawl"]
    if between:
        return "SPA 壳后、run_custom_script 之前调用了 browse_and_crawl（硬规则禁止）"
    return None


CASES = [
    # -- 检索优先（RETRIEVE-FIRST）--
    Case(
        id="01_retrieve_first_hit",
        rule="RETRIEVE-FIRST (HARD)：KB 命中即从档案作答，不重爬",
        user="帮我抓 https://blog.example.com/post/123 这篇文章的正文",
        script={"search_knowledge": [KB_HIT]},
        expect_seq=["search_knowledge"],
        forbid=["crawl_webpage", "browse_and_crawl", "extract_list_paged",
                "crawl4ai_deep_crawl"],
        reply_re=r"2031\s*年\s*4\s*月",
    ),
    Case(
        id="02_retrieve_first_miss",
        rule="RETRIEVE-FIRST (HARD)：先查 KB，未命中才抓取",
        user="帮我抓 https://blog.example.com/post/123 这篇文章的正文",
        script={"search_knowledge": [KB_MISS],
                "crawl_webpage": [HTML_POST],
                "extract_content": [EXTRACT_OK]},
        expect_seq=["search_knowledge", "crawl_webpage"],
        forbid=["browser_use_navigate"],
    ),

    # -- 失败换路（SPA 壳 / 3 连败 / 壁纸站）--
    Case(
        id="03_spa_shell",
        rule="SPA 壳视为失败：下一步直接 run_custom_script，不许 browse_and_crawl",
        user="抓取 https://spa.example.com/app 的页面内容",
        script={"search_knowledge": [KB_MISS],
                "list_site_profiles": ["NO_PROFILE: 无该站点档案（eval stub）。"],
                "crawl_webpage": [SPA_SHELL],
                "run_custom_script": [SCRIPT_OK_SIMPLE]},
        expect_seq=["crawl_webpage", "run_custom_script"],
        check=_check_no_browse_before_script,
    ),
    Case(
        id="04_three_failures",
        rule="2 个内置工具连败后必须 run_custom_script，不许试第 3 个内置工具",
        user="抓取 https://blocked.example.com/list 页面的内容",
        script={"search_knowledge": [KB_MISS],
                "list_site_profiles": ["NO_PROFILE: 无该站点档案（eval stub）。"],
                "crawl_webpage": [
                    "ERROR: connection refused after 3 retries — "
                    "target site blocked our IP（eval stub）"],
                "browse_and_crawl": [
                    "ERROR: browser fetch failed — page blocked by anti-crawl, "
                    "rendered body is empty（eval stub）"],
                "run_custom_script": [SCRIPT_OK_SIMPLE]},
        expect_seq=["crawl_webpage", "browse_and_crawl", "run_custom_script"],
        forbid=["crawl4ai_deep_crawl", "browser_use_navigate", "extract_content"],
    ),
    Case(
        id="05_wallpaper_fail",
        rule="壁纸站 extract_wallpaper_list 返回 0 条 → 直接 run_custom_script",
        user="帮我抓 https://wallpaper.example.com/list 这个壁纸站的图片链接",
        script={"search_knowledge": [KB_MISS],
                "list_site_profiles": ["NO_PROFILE: 无该站点档案（eval stub）。"],
                "extract_wallpaper_list": [WALLPAPER_ZERO],
                "run_custom_script": [SCRIPT_WALLPAPER_OK]},
        expect_seq=["extract_wallpaper_list", "run_custom_script"],
        forbid=["crawl_webpage", "browse_and_crawl"],
    ),

    # -- 档案入库询问（ARCHIVE-ASK）--
    Case(
        id="06_archive_ask_multipage",
        rule="ARCHIVE-ASK：多页抓取完成后必须先问「存入档案」再 save_record",
        user="把 https://news.example.com/list 这个列表每一页的内容都抓下来",
        script={"search_knowledge": [KB_MISS],
                "list_site_profiles": ["NO_PROFILE: 无该站点档案（eval stub）。"],
                "extract_list_paged": [LIST_PAGED_OK],
                "save_record": ["SAVED: 记录已存入知识库 id=101（eval stub）。"]},
        ask=["存入档案"],
        expect_seq=["extract_list_paged", "ask_user", "save_record"],
        arg_contains=[("ask_user", "question", "存入档案")],
    ),
    Case(
        id="07_archive_ask_single_page",
        rule="ARCHIVE-ASK：单页速览不询问、不入库、不写文件",
        user="看一眼 https://blog.example.com/post/9 这篇文章讲了什么，速览就行",
        script={"search_knowledge": [KB_MISS],
                "list_site_profiles": ["NO_PROFILE: 无该站点档案（eval stub）。"],
                "crawl_webpage": [HTML_POST],
                "extract_content": [EXTRACT_OK]},
        forbid=["ask_user", "save_record", "save_to_file"],
    ),

    # -- 置信度升级（SUPERVISOR）--
    Case(
        id="08_low_confidence_upgrade",
        rule="LOW CONFIDENCE：不入库不展示，立即 browse_and_crawl 重抓再提取",
        user="抓取 https://tricky.example.com/article 的正文内容",
        script={"search_knowledge": [KB_MISS],
                "list_site_profiles": ["NO_PROFILE: 无该站点档案（eval stub）。"],
                "crawl_webpage": [HTML_POST],
                "extract_content": [EXTRACT_LOW, EXTRACT_OK],
                "browse_and_crawl": [HTML_POST]},
        expect_seq=["crawl_webpage", "extract_content", "browse_and_crawl",
                    "extract_content"],
        forbid=["save_record"],
    ),

    # -- MCP 授权与配置（ASK-USER / MCP ADMIN）--
    Case(
        id="09_mcp_consent",
        rule="MCP 服务未运行：check_mcp_status 诊断后必须 ask_user，禁止静默拉起",
        user="用抓包工具帮我分析 https://app.example.com 的接口加密",
        script={"check_mcp_status": [MCP_UNREACHABLE]},
        ask=["暂不打开"],
        expect_seq=["check_mcp_status", "ask_user"],
        forbid=["wait_capture_ready"],
    ),
    Case(
        id="10_mcp_add_confirm",
        rule="MCP ADMIN：先 list_mcp_servers 防重名，ask_user 展示完整配置后才 add",
        user=('帮我添加一个 MCP server：名字叫 demo，stdio 方式，'
              'command 是 node，参数 ["server.js"]，请帮我接入'),
        script={"list_mcp_servers": ["当前配置 0 个 MCP server（eval stub）。"],
                "add_mcp_server": [MCP_ADDED]},
        ask=["添加"],
        expect_seq=["list_mcp_servers", "ask_user", "add_mcp_server"],
        count={"add_mcp_server": {"==": 1}},
        arg_contains=[("ask_user", "question", "stdio"),
                      ("ask_user", "question", "node")],
    ),

    # -- 沟通契约（语言 / 零工具）--
    Case(
        id="11_language_rule",
        rule="LANGUAGE RULE：英文提问也默认中文回复",
        user="Please fetch https://plain.example.com/page and tell me what it says.",
        script={"search_knowledge": [KB_MISS],
                "list_site_profiles": ["NO_PROFILE: 无该站点档案（eval stub）。"],
                "crawl_webpage": [HTML_POST],
                "extract_content": [EXTRACT_OK]},
        check=_check_chinese_reply,
    ),

    Case(
        id="12_chitchat_zero_tools",
        rule="TOOL-USE DECISION：寒暄零工具调用",
        user="你好呀，你是谁？你都能干什么？",
        total_max=0,
    ),
    Case(
        id="13_concept_zero_tools",
        rule="TOOL-USE DECISION：概念/代码问答零工具调用（无 URL 无执行动词）",
        user="BeautifulSoup 怎么翻页爬取？给我一个示例代码看看写法",
        total_max=0,
    ),

    # -- 直达工作流（WeRead）--
    Case(
        id="14_weread_direct",
        rule="WeRead 工作流：直达 list_weread_chapters，Cookie 未配置时 ask_user 索取",
        user="帮我看看这本书有哪些章节 https://weread.qq.com/web/reader/abc123",
        script={"list_weread_chapters": [WEREAD_NO_COOKIE]},
        ask=["好的，我稍后粘贴 Cookie"],
        expect_seq=["list_weread_chapters", "ask_user"],
        forbid=["crawl_webpage", "browse_and_crawl"],
        arg_contains=[("ask_user", "question", "Cookie")],
    ),

    # -- 批量优先（BATCH-FIRST / TOOL BUDGET）--
    Case(
        id="15_batch_first",
        rule="BATCH-FIRST：30 章批量 = 1 次 run_custom_script 内循环，总调用受限",
        user="把 https://novel.example.com/book/1 这本书的 30 个章节全部抓下来存成文件",
        script={"search_knowledge": [KB_MISS],
                "list_site_profiles": ["NO_PROFILE: 无该站点档案（eval stub）。"],
                "crawl_webpage": [HTML_CHAPTER_LIST + CHAPTER_LIST_HTML_NOTE],
                "run_custom_script": [
                    "BATCH DONE: 30 章全部抓取成功（eval stub），"
                    "已按章保存为 第1章.md ~ 第30章.md，"
                    "汇总：总字数 86000，失败 0 章。"],
                "save_to_file": ["OK: 已写入 第1-30章合并.md（eval stub，未落盘）。"]},
        ask=["存入档案"],
        count={"run_custom_script": {"==": 1}},
        total_max=8,
    ),
]
