"""脚本强制中间件 — 追踪工具失败，3 次失败后强制切换到自定义脚本。

解决的问题：
    Agent 调用 72 次工具仍未成功抓取网站内容，陷入"尝试不同工具 → 失败 →
    再尝试另一个工具"的死循环，永远不会调用 run_custom_script。

    本中间件在 wrap_tool_call 中检查工具结果是否为失败状态，累计 3 次后
    在 wrap_model_call 中注入 SystemMessage，强制要求 AI 使用自定义脚本。

检测为"失败"的条件：
    - 工具返回以 "ERROR"、"FAILED"、"[ERR]" 开头
    - 返回包含 "LOW CONFIDENCE"
    - 返回内容为空或极短（< 50 字符）
    - 返回 "SPA shell"、"blocked"、"anti-crawl" 等关键词

强制触发阈值：
    - 3 次失败 → 注入 SystemMessage 强制使用 run_custom_script
    - 5 次失败 → 更强的 SystemMessage，明确禁止调用任何预制工具
"""
from __future__ import annotations

import re
from collections.abc import Callable

from langchain_core.messages import SystemMessage, ToolMessage
from langchain.agents.middleware import AgentMiddleware, ModelRequest, ToolCallRequest


# 付费墙/认证墙关键词 — 不视为"失败"，视为"需要停止并上报"的终止态，
# 避免误触发 ScriptForcer 强制 run_custom_script 形成死循环。
_PAYWALL_PATTERNS = [
    re.compile(r"vip account may be required", re.IGNORECASE),
    re.compile(r"this chapter is vip-locked", re.IGNORECASE),
    re.compile(r"proxy api failed", re.IGNORECASE),
]

# 滑块/人机验证码特征库（保留用于识别/记录，当前不再做任何强制拦截）。
# 现代滑块（极验 geetest v2/v3/v4、阿里云盾 nocaptcha、腾讯防水墙 TCaptcha、
# 网易易盾 yidun 等）的通过率确实受轨迹/设备指纹风控影响，但按上层规则，
# 识别到之后不自动注入"停止脚本、走 A/B/C"这种硬提示——让 Agent 自由发挥：
# 可以试 requests.session 持久化 + Playwright 轨迹 + ddddocr 缺口 + cv2 模板匹配
# + 自定义行为脚本 + solve_slider_via_browser 改参多跑……什么组合都允许。
# 真的想不出办法再让 Agent 自己判断是否要兜底存档 Cookie/问用户。
_SLIDER_CAPTCHA_PATTERNS = [
    # 极验 geetest
    re.compile(r"geetest[_\-]?\w*", re.IGNORECASE),
    re.compile(r"\bgt\.js\b|geetest\.com|gt\.geetest", re.IGNORECASE),
    # 阿里云盾
    re.compile(r"nocaptcha|aliyun.?captcha|nc_iconfont|\.nc-?", re.IGNORECASE),
    # 腾讯防水墙
    re.compile(r"tcaptcha|tcaptcha_iframe|\.tdc\b|captcha\.qq\.com", re.IGNORECASE),
    # 网易易盾
    re.compile(r"yidun|necaptcha|c\.dun\.163", re.IGNORECASE),
    # 通用滑块 / 拼图 / 滑动验证
    re.compile(r"slider[_\- ]?captcha|drag[_\- ]?captcha|puzzle[_\- ]?captcha", re.IGNORECASE),
    re.compile(r"滑动验证|拖动滑块|拖动完成拼图|滑块验证|完成验证", re.IGNORECASE),
    re.compile(r"captcha[_\- ]?(slider|puzzle|verify)|verify[_\- ]?slider", re.IGNORECASE),
]


def _is_slider_captcha(result: str) -> bool:
    """检测工具返回结果里是否出现滑块/人机验证码页的典型特征。

    只做"识别"用，当前不会由此触发任何硬注入/强制跳转。
    Agent 想尝试什么脚本策略、想把验证码 URL 单独拆出来处理、
    想在 helper 上改轨迹/改参数多跑多少次都自由。
    """
    if not result:
        return False
    text = str(result)
    for pat in _SLIDER_CAPTCHA_PATTERNS:
        if pat.search(text):
            return True
    return False

_FAILURE_PATTERNS = [
    re.compile(r"^\s*ERROR:", re.IGNORECASE),
    re.compile(r"^\s*FAILED", re.IGNORECASE),
    re.compile(r"^\s*\[ERR\]", re.IGNORECASE),
    re.compile(r"LOW CONFIDENCE", re.IGNORECASE),
    re.compile(r"SPA_SHELL_DETECTED", re.IGNORECASE),
    re.compile(r"SPA shell", re.IGNORECASE),
    re.compile(r"anti.crawl|blocked|rate.limit", re.IGNORECASE),
    re.compile(r"no real content|empty page|login required", re.IGNORECASE),
    re.compile(r"font.encrypted|font encryption", re.IGNORECASE),
    re.compile(r"connection error|timeout|timed out", re.IGNORECASE),
    re.compile(r"crawl failed", re.IGNORECASE),
    re.compile(r"browse failed", re.IGNORECASE),
    re.compile(r"failed:", re.IGNORECASE),
    re.compile(r"no detail-page links found", re.IGNORECASE),
]

_SUCCESS_PATTERNS = [
    re.compile(r"^\s*SAVED:", re.IGNORECASE),
    re.compile(r"^\s*FOUND:", re.IGNORECASE),
    re.compile(r"^\s*NO_PROFILE:", re.IGNORECASE),
    re.compile(r"^\s*\{"),
    re.compile(r"CONFIDENCE:", re.IGNORECASE),
]


def _is_paywall(result: str) -> bool:
    """判断工具结果是否遇到付费墙/认证墙/代理失败。

    这类结果不算"失败"（不触发 ScriptForcer 强制脚本回退），
    但被标记为"终止态"——应该立刻向用户上报需求，而不是继续调用工具形成死循环。
    """
    if not result:
        return False
    text = str(result)
    for pat in _PAYWALL_PATTERNS:
        if pat.search(text):
            return True
    return False


def _is_failure(result: str) -> bool:
    """判断工具结果是否为失败状态。

    付费墙结果不算失败（避免强制 run_custom_script 死循环）。
    """
    if not result or len(str(result).strip()) < 50:
        return True
    text = str(result)
    if _is_paywall(text):
        return False
    for pat in _FAILURE_PATTERNS:
        if pat.search(text):
            return True
    return False


def _is_success(result: str) -> bool:
    """判断工具结果是否为成功状态。"""
    if not result:
        return False
    text = str(result)
    for pat in _SUCCESS_PATTERNS:
        if pat.search(text):
            return True
    return False


class ScriptForcerMiddleware(AgentMiddleware):
    """追踪工具调用结果，失败 3 次后强制 AI 使用 run_custom_script。

    同时检测"假成功"死循环：工具返回有效数据但任务无进展时，
    通过总调用上限和连续同工具上限及时打断。

    使用方法：
        agent = create_agent(
            model=llm,
            tools=tools,
            system_prompt=SYSTEM_PROMPT,
            middleware=[ScriptForcerMiddleware()],
        )

    与 TrimHistoryMiddleware 配合使用时放在后面：
        middleware=[trim_middleware, ScriptForcerMiddleware()]
    """

    def __init__(self, failure_threshold: int = 3, hard_limit: int = 5,
                 max_total_calls: int = 20, max_consecutive_same: int = 4,
                 max_run_custom_script: int = 4, script_stall_threshold: int = 2):
        """Args:
            failure_threshold: 连续失败多少次后注入强制脚本指令
            hard_limit: 失败多少次后注入最强指令（禁止预制工具）
            max_total_calls: 单轮总工具调用上限，超过后强制总结回答
            max_consecutive_same: 连续调用同一工具的上限，超过后强制换策略
            max_run_custom_script: 单轮 run_custom_script 调用上限（默认 4 次），超过则
                强制 AI 必须进入 S4/S5（browser_render 或向用户要 Cookie），禁止再
                写 S1/S2 的 requests 脚本。目标：防 session 758be2f09ce1 式"连写 14
                个 requests 脚本却没有实质升级"。
            script_stall_threshold: 连续多少次 run_custom_script 返回正文长度差 < 10%
                （又没触发 paywall/success）视为"结果停滞"。达到即发升级提示。
        """
        self.failure_threshold = failure_threshold
        self.hard_limit = hard_limit
        self.max_total_calls = max_total_calls
        self.max_consecutive_same = max_consecutive_same
        self.max_run_custom_script = max_run_custom_script
        self.script_stall_threshold = script_stall_threshold
        self._reset()

    def _reset(self):
        self._consecutive_failures = 0
        self._total_tool_calls = 0
        self._force_script_next = False
        self._force_script_hard = False
        self._consecutive_same_tool = ""
        self._consecutive_same_count = 0
        self._force_summary = False
        #  run_custom_script 专用治理
        self._run_custom_script_count = 0
        self._last_script_body_len: int | None = None  # 上次脚本返回的预估正文长度
        self._script_stall_count = 0                   # 连续停滞次数
        self._force_script_escalate = False            # 下一次 LLM 调用时发升级提示
        # 滑块验证码检测（保留标志位 + 特征检测函数本体：现在不触发任何硬注入；
        #  将来要恢复护栏时，重新在 after_tool 里 set 这个标志、并在 build_force_msg
        #  加对应分支即可，不用重写特征库）
        self._force_captcha_alert = False              # 默认 False；当前无代码会把它置 True
        # 开拓者心态护栏：同一 URL 反复试不同方法但仍无突破 → 注入"探索穷举"提示
        self._url_attempt_map: dict[str, list[str]] = {}  # {url: [tool_name, ...]}
        self._url_body_len_map: dict[str, list[int]] = {}  # {url: [body_len, ...]}
        self._force_exploration_alert = False             # 触发探索穷举提示标志
        self._exploration_alert_url: str = ""              # 触发的 URL
        self._exploration_tried_paths: list[str] = []     # 已经尝试过的 P1-P10 标签

    def reset(self):
        """公开重置入口：server.py 每轮开始时调用，替代跨模块访问私有 _reset()。"""
        self._reset()

    def wrap_model_call(self, request: ModelRequest, handler: Callable):
        """在 LLM 调用前检查是否需要注入强制消息。

        缓存安全：强制消息追加到 messages 末尾，不插入头部。
        DeepSeek 缓存基于前缀匹配 — 在头部插入会打碎整个前缀，命中率归零。
        追加到末尾只增加 miss tokens（新消息），不影响已缓存的前缀。
        """
        # 任何 ScriptForcer 异常都不能影响 LLM 正常调用 → 外层兜底 try/except。
        try:
            force_msg = self._build_force_message()
            if force_msg is not None:
                request.messages = list(request.messages) + [force_msg]
        except Exception as exc:  # pragma: no cover - 防御性代码，不可达
            # 用 warnings.warn 而非 print/logging：不引入新依赖、不污染 stdout
            import warnings
            warnings.warn(f"ScriptForcer.wrap_model_call swallowed: {exc!r}")
        return handler(request)

    def _build_force_message(self):
        """独立出强制消息构造：便于单元测试 + 单一职责。"""
        force_msg = None

        if self._force_summary:
            force_msg = SystemMessage(content=(
                f"[FORCED WRAP-UP] You have already made {self._total_tool_calls} tool calls this turn "
                f"but have not yet given the user a final answer.\n\n"
                "You must now stop calling ANY tools and write the FINAL ANSWER from the data already collected: "
                "summarize what was found, which files were downloaded/saved, and what remains pending.\n\n"
                "No more tool calls — answer the user directly. (Reply in Chinese per the LANGUAGE RULE.)"
            ))
            self._force_summary = False

        elif self._force_exploration_alert:
            # 开拓者心态护栏：同一 URL 已经试了多次但无突破 → 列出 P1-P10 中还没试的路径
            url = self._exploration_alert_url or "the target URL"
            tried = self._exploration_tried_paths or []
            all_paths = [
                ("P1", "Mirrors/reposts (cnblogs/jianshu/juejin/zhihu/WeChat official accounts)"),
                ("P2", "Wayback Machine (web.archive.org/web/*/URL)"),
                ("P3", "Google/Bing cache (cache:URL / webcache.googleusercontent.com)"),
                ("P4", "RSS / sitemap (/rss /feed /sitemap.xml /atom.xml)"),
                ("P5", "Mobile API (m. subdomain / mobile UA / app endpoints)"),
                ("P6", "Unauthenticated internal APIs (inspect JS bundles for /api/ /internal/)"),
                ("P7", "Third-party aggregators (Jina Reader r.jina.ai/URL / 12ft.io / Bing translator proxy)"),
                ("P8", "CDN cache direct (nslookup for CDN IPs, keep the original Host header)"),
                ("P9", "Same-site legacy subdomains (archive./old./legacy.)"),
                ("P10", "Ask the user for credentials (Cookie/VIP subscription)"),
            ]
            untried = [p for p in all_paths if p[0] not in tried]
            lines = [
                f"[PIONEER ESCALATION] target URL={url}",
                f"You have already tried {len(tried)} route(s) against this wall without a breakthrough: {', '.join(tried) if tried else '(none)'}",
                "",
                "Every wall has an alternative route, but the SAME wall must not be hit more than twice —",
                "continuing to ram the same URL wastes budget. Switch IMMEDIATELY to one of these UNTRIED legitimate routes:",
                "",
            ]
            if untried:
                lines.append("Untried legitimate alternative routes (ordered by success rate + compliance, 1 attempt each):")
                for tag, desc in untried[:5]:
                    lines.append(f"  {tag}: {desc}")
                if len(untried) > 5:
                    lines.append(f"  ...and {len(untried)-5} more ({', '.join(p[0] for p in untried[5:])})")
                lines.append("")
                lines.append("Your next run_custom_script MUST implement one of the untried routes above. Scripts that directly requests.get the original URL are FORBIDDEN.")
            else:
                lines.append("All 10 legitimate alternative routes are exhausted without a breakthrough. This content is most likely server-side authenticated with no public archive.")
                lines.append("")
                lines.append("Stop trying now and ask the user for credentials:")
                lines.append('  "本内容服务器端真鉴权且无任何公开存档/镜像，需要您提供 Cookie/VIP 订阅才能取到。"')
                lines.append("After receiving credentials, call save_site_profile(origin, cookies=<pasted>) to archive; they will be reused automatically next time.")
            force_msg = SystemMessage(content="\n".join(lines))
            self._force_exploration_alert = False
            self._exploration_alert_url = ""
            # 不清空 _exploration_tried_paths，避免用户给完凭证后又原地打转

        elif self._run_custom_script_count >= self.max_run_custom_script or self._force_script_escalate:
            # 脚本次数用完或正文长度停滞 → 强制升级到 S4，禁止再写 requests 直连脚本
            stalled = self._script_stall_count
            called = self._run_custom_script_count
            lines = [
                f"[SCRIPT LADDER ESCALATION] {called} custom script(s) called; body length stalled for {stalled} round(s).",
                "",
                "S1/S2 (direct requests) strategies are exhausted. Across multiple scripts the free-preview body length has not grown materially —",
                "plain HTTP requests can no longer break through this paywall/anti-crawl barrier.",
                "",
                "Your next script may ONLY use S3 or S4 (tools available inside the run_custom_script environment):",
                "  S3: decrypt_js_eval_html(html) — use when the response body is a JS-eval obfuscated page.",
                "  S4: browser_render(url, wait_ms=5000, wait_selector='#content_views')",
                "       — full Playwright Chromium rendering (pass stored cookies via get_site_profile's cookies field).",
                "  S4+cookies: profile = get_site_profile('https://<domain>') ;",
                "              browser_render(url, cookies=profile.get('cookies',''))",
                "",
                "FORBIDDEN: writing another direct requests.get(...) script; FORBIDDEN: retrying with a different UA / Referer / external proxy.",
                "If browser_render(url) still returns a VIP mask / locked content (body length grew < 5%):",
                "stop writing scripts immediately and offer the user two choices: (a) provide login Cookie / VIP session string to archive in the site profile;",
                "(b) accept saving only the free preview already fetched."
            ]
            if called >= self.max_run_custom_script:
                lines.append("")
                lines.append(f"[HARD CAP] At most {self.max_run_custom_script} run_custom_script calls per turn.")
                lines.append("This is the LAST custom script call available this turn.")
            force_msg = SystemMessage(content="\n".join(lines))
            self._force_script_escalate = False  # 只注入一次；若又撞继续计数还会再触发
            # 不重置 _run_custom_script_count，下次再调就命中 _force_summary 前的阈值

        elif self._force_script_hard:
            fail = self._consecutive_failures
            force_msg = SystemMessage(content=(
                "[BUILT-IN TOOLS VOIDED] — you MUST write a custom script now.\n\n"
                f"You have failed {fail} built-in tool calls in a row on this site; every built-in tool has failed.\n"
                "You are FORBIDDEN from calling any more built-in tools (crawl_webpage, browse_and_crawl, "
                "extract_content, extract_list, extract_wallpaper_list, etc.).\n\n"
                "Your only next step is run_custom_script with a Python script that:\n"
                "1. Fetches the page HTML with requests\n"
                "2. Parses it with BeautifulSoup\n"
                "3. Handles site-specific logic (endpoints, headers, etc.)\n"
                "4. Prints all results with print()\n\n"
                "The run_custom_script environment ships with: requests, BeautifulSoup, json, re, sys, os, "
                "Path, hashlib, base64, urlparse. PROJECT_ROOT is available.\n\n"
                "If you dare call one more built-in tool instead of run_custom_script, this task FAILS."
            ))

        elif self._force_script_next:
            fail = self._consecutive_failures
            force_msg = SystemMessage(content=(
                f"[SCRIPT UPGRADE ADVISED] You have failed {fail} built-in tool calls in a row on this site. "
                "Built-in tools like crawl_webpage / browse_and_crawl / extract_content no longer work on it.\n\n"
                "Your next step MUST be run_custom_script to write and execute a custom Python script. "
                "This is not optional — do not call another built-in tool.\n\n"
                "The script should fetch and parse the page with requests + BeautifulSoup and print results with print(). "
                "Environment ships with: requests, bs4, json, re, Path, datetime, PROJECT_ROOT.\n\n"
                "After script success, ALWAYS call save_site_profile to archive the script for future reuse."
            ))
            self._force_script_next = False

        elif self._consecutive_same_count >= self.max_consecutive_same:
            force_msg = SystemMessage(content=(
                f"[WARNING: SAME-TOOL LOOP] You have called '{self._consecutive_same_tool}' "
                f"{self._consecutive_same_count} times in a row.\n\n"
                "This is a pre-death-loop signal. Repeating the same method yields nothing — switch strategy NOW:\n"
                "- If calling run_custom_script → switch to download_images or save_to_file to wrap up\n"
                "- If calling built-in tools → switch to run_custom_script with a genuinely different approach\n"
                "- If the data on hand is already sufficient → give the final answer now\n\n"
                "You may NOT call the same tool again."
            ))

        return force_msg

    @staticmethod
    def _extract_tool_name(tool_call) -> str:
        """兼容 dict / pydantic ToolCall 两种格式取 name。"""
        if tool_call is None:
            return ""
        # pydantic / dataclass / namedtuple 对象式：优先 .name 属性（LangChain v0.2+）
        name = getattr(tool_call, "name", None)
        if name:
            return str(name)
        # dict 格式：旧版 / 部分场景
        if isinstance(tool_call, dict):
            return str(tool_call.get("name") or "")
        # 兜底：tool_call["name"] 若支持
        try:
            return str(tool_call["name"])
        except Exception:
            return ""

    def wrap_tool_call(self, request: ToolCallRequest, handler: Callable):
        """在工具执行前后做治理：计数/停滞/升级标志。
        关键约束：此方法**任何异常**必须被吞掉、直接放行 handler(request)，
        绝对不允许因为中间件本身 bug 导致 tool 整体失败。
        """
        tool_call = getattr(request, "tool_call", None)
        tool_name = self._extract_tool_name(tool_call)

        # ① 前置阶段（handler 调用前）：仅做计数，任何异常跳过
        try:
            self._total_tool_calls += 1
            if tool_name == self._consecutive_same_tool:
                self._consecutive_same_count += 1
            else:
                self._consecutive_same_tool = tool_name
                self._consecutive_same_count = 1
            if self._total_tool_calls >= self.max_total_calls:
                self._force_summary = True
        except Exception as exc:  # pragma: no cover
            import warnings
            warnings.warn(f"ScriptForcer pre-tool swallowed: {exc!r}")

        # ② 执行真实工具
        try:
            result = handler(request)
        except Exception:
            raise  # 工具自己的异常必须向上冒泡给框架处理，不要吞

        # ③ 后置阶段（handler 返回后）：检测结果 / 治理
        try:
            # 取结果内容：兼容 ToolMessage 对象 / 字符串 / 其他
            result_content = ""
            if isinstance(result, ToolMessage):
                result_content = str(result.content) if result.content else ""
            elif isinstance(result, str):
                result_content = result
            elif result is not None:
                result_content = str(result)

            # 开拓者心态护栏：跟踪每个 URL 的尝试次数 + body_len 历史
            # 检测"同一 URL 反复试不同方法但仍无突破" → 触发 EXPLORATION ESCALATION
            target_url = self._extract_target_url(tool_call)
            if target_url:
                self._track_url_attempt(target_url, tool_name, result_content)
                # 触发条件：同一 URL 累计尝试 ≥ 4 次 且 该 URL 历史最大 body_len < 4000
                # 4 次已足以覆盖 S1+S2+S3+S4 全部 S 级 + 至少 1 条 P 路径
                attempts = self._url_attempt_map.get(target_url, [])
                max_body = max(self._url_body_len_map.get(target_url, [0]))
                if len(attempts) >= 4 and max_body < 4000 and not self._force_exploration_alert:
                    self._force_exploration_alert = True
                    self._exploration_alert_url = target_url
                    # 自动推断已试过的 P 路径标签（基于已尝试工具名）
                    self._exploration_tried_paths = self._infer_tried_paths(attempts)

            # 付费墙命中：不重置失败，但仍然让 run_custom_script 分支继续执行
            if _is_paywall(result_content) and tool_name != "run_custom_script":
                return result

            # [2026-09 按上层"不要限制创造力"规则取消滑块验证码自动注入]
            # 旧逻辑：_is_slider_captcha() 命中 → set _force_captcha_alert → 下次 LLM 被强制插
            # "You are FORBIDDEN ... MUST pick A/B/C" 那段。现在删除这条路径：
            #   · 不再 set _force_captcha_alert（永远保持 __init__ 默认 False）
            #   · build_force_msg 里对应的 elif 分支也已整个删除
            # 结果：滑块验证码命中后，Agent 看到页面里的滑块信号，由 SYSTEM_PROMPT 里的
            #   "自由尝试各种手段组合"软建议自己决定怎么干，不再有监督员强制打断。
            # _is_slider_captcha 检测函数本体仍然保留（以后要恢复护栏直接调用即可）。

            if tool_name == "run_custom_script":
                self._apply_script_governance(result_content)
            else:
                if _is_success(result_content):
                    self._consecutive_failures = 0
                    self._force_script_next = False
                    self._force_script_hard = False
                elif _is_failure(result_content):
                    self._consecutive_failures += 1
                    if self._consecutive_failures >= self.hard_limit:
                        self._force_script_hard = True
                        self._force_script_next = False
                    elif self._consecutive_failures >= self.failure_threshold:
                        self._force_script_next = True
        except Exception as exc:  # pragma: no cover - 防御性
            import warnings
            warnings.warn(f"ScriptForcer post-tool swallowed: {exc!r}")

        return result

    @staticmethod
    def _extract_target_url(tool_call) -> str:
        """从 tool_call.args 里抽出目标 URL（兼容 dict / pydantic）。
        用于开拓者心态护栏按 URL 维度跟踪尝试次数。
        """
        if tool_call is None:
            return ""
        # 取 args
        args = None
        if isinstance(tool_call, dict):
            args = tool_call.get("args") or {}
        else:
            args = getattr(tool_call, "args", None) or {}
        if not isinstance(args, dict):
            return ""
        # crawl_webpage/browse_and_crawl/extract_content 都用 url 字段；run_custom_script 用 code
        for key in ("url", "source_url", "target_url"):
            v = args.get(key)
            if v and isinstance(v, str) and v.startswith("http"):
                return v
        # run_custom_script：在 code 里搜 https?:// 链接，取第一个为目标 URL
        code = args.get("code") or ""
        if isinstance(code, str):
            m = re.search(r"https?://[^\s\"'<>]+", code)
            if m:
                # 截到第一个 ? & 之前的 path，避免 query 干扰
                u = m.group(0)
                # 去掉尾部的常见字符
                u = u.rstrip("'\")")
                return u
        return ""

    def _track_url_attempt(self, url: str, tool_name: str, result_content: str) -> None:
        """记录该 URL 的尝试次数 + 每次结果正文长度。"""
        try:
            self._url_attempt_map.setdefault(url, []).append(tool_name)
            # 抓 body_len（与 _apply_script_governance 同逻辑）
            body_len = 0
            for pat in (r"BODY LEN:\s*(\d+)", r"LEN:\s*(\d+)", r"HTML_LEN:\s*(\d+)",
                        r"content len[=: ]\s*(\d+)", r"TEXT_LEN:\s*(\d+)"):
                m = re.search(pat, result_content, re.I)
                if m:
                    try:
                        body_len = int(m.group(1))
                        break
                    except ValueError:
                        pass
            if body_len == 0:
                # 兜底：用 cleaned result_content 字符数
                cleaned = re.sub(r"\[STDERR\].*$", "", result_content, flags=re.S)
                cleaned = re.sub(r"\[EXIT CODE \d+\]\s*", "", cleaned)
                body_len = max(0, len(cleaned.strip()))
            self._url_body_len_map.setdefault(url, []).append(body_len)
        except Exception:
            pass

    @staticmethod
    def _infer_tried_paths(attempted_tools: list) -> list:
        """根据已尝试过的工具名 / 脚本内容，推断已经走过的 P 路径标签。
        保守推断：宁可少标已试（让 AI 多看到一些可选路径）也不要漏标（避免重复试）。
        """
        tried = set()
        joined = " ".join(attempted_tools).lower()
        # run_custom_script 的脚本里若含特定 host/keyword → 标记对应 P
        for tool_name in attempted_tools:
            tl = (tool_name or "").lower()
            if "crawl_webpage" in tl or "browse_and_crawl" in tl:
                tried.add("P5")  # 通常第一次直连 = S1 requests = 含移动端尝试意识薄弱
            if "list_site_profiles" in tl:
                tried.add("P10")  # 查 cookie 档案 ≈ 准备走 P10 路径
            if "save_site_profile" in tl:
                tried.add("P10")
        # 字符串匹配（针对 run_custom_script 的 code 内容简略判定）
        if "web.archive.org" in joined or "wayback" in joined:
            tried.add("P2")
        if "webcache.googleusercontent" in joined or "cache:" in joined:
            tried.add("P3")
        if "/rss" in joined or "/feed" in joined or "/sitemap" in joined or "/atom.xml" in joined:
            tried.add("P4")
        if "m." in joined and "subdomain" not in joined:
            tried.add("P5")
        if "r.jina.ai" in joined or "12ft.io" in joined or " translator" in joined:
            tried.add("P7")
        if "/api/" in joined or "/internal" in joined:
            tried.add("P6")
        return sorted(tried)

    def _apply_script_governance(self, result_content: str) -> None:
        """run_custom_script 专用：计数 + 正文停滞检测 + 升级标志。独立方法便于单测。"""
        self._run_custom_script_count += 1

        # ===== 正文长度停滞检测 =====
        body_len: int | None = None
        for pat in (r"BODY LEN:\s*(\d+)", r"LEN:\s*(\d+)", r"HTML_LEN:\s*(\d+)",
                    r"content len[=: ]\s*(\d+)", r"TEXT_LEN:\s*(\d+)"):
            m = re.search(pat, result_content, re.I)
            if m:
                try:
                    body_len = int(m.group(1))
                    break
                except ValueError:
                    body_len = None
        if body_len is None:
            cleaned = result_content
            cleaned = re.sub(r"\[STDERR\].*$", "", cleaned, flags=re.S)
            cleaned = re.sub(r"\[EXIT CODE \d+\]\s*", "", cleaned)
            body_len = max(0, len(cleaned.strip()))

        is_stalled = False
        if self._last_script_body_len is not None and self._last_script_body_len > 0 and body_len > 0:
            delta = abs(body_len - self._last_script_body_len)
            rel = delta / self._last_script_body_len
            if rel < 0.10 and max(body_len, self._last_script_body_len) < 4000:
                is_stalled = True
        self._last_script_body_len = body_len

        if is_stalled:
            self._script_stall_count += 1
            if self._script_stall_count >= self.script_stall_threshold:
                self._force_script_escalate = True
        elif not _is_paywall(result_content) and body_len >= 4000:
            self._script_stall_count = 0

        if self._run_custom_script_count >= self.max_run_custom_script:
            self._force_script_escalate = True

        if _is_failure(result_content):
            # 脚本自身崩溃：不清零失败计数、但重置"强制换工具"提示避免 ping-pong
            self._consecutive_failures = 0
            self._force_script_next = False
            self._force_script_hard = False
