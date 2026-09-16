"""模拟登录工具 — Playwright 自动化表单登录 + Cookie 持久化到站点档案。

设计意图：
    - 简单站直接 save_site_profile(origin, cookies=<用户给的>)，不调本工具。
    - 复杂站（用户只给用户名密码、不让用户手动 F12 复制 Cookie）才用 login_site
      跑 Playwright 自动登录流程，成功后立刻 upsert_site 存档。
    - 登录态失效检查（check_login_status）用已存 Cookie 请求一个需登录页面，
      看是否被重定向到登录页 / 出现登录表单。
    - 走 Playwright（已有依赖），不需要额外装包。

零侵入热路径：不在主循环自动检查登录态；Agent 按需调本工具。
"""
from __future__ import annotations

import asyncio
import re
from datetime import datetime
from urllib.parse import urlparse, unquote

import requests
from langchain_core.tools import tool

from crawagent.tools.site_profile_tool import (
    upsert_site, get_site_cookies, _normalize_origin,
)


# ---------------------------------------------------------------------------
# helper
# ---------------------------------------------------------------------------

_LOGIN_URL_PATTERNS = re.compile(
    r"/login|/signin|/sign-in|/account/login|/auth/login|/user/login",
    re.IGNORECASE,
)

_LOGIN_TEXT_PATTERNS = [
    "请登录", "登录后查看", "登录后查看", "请先登录", "需要登录",
    "Please log in", "Please sign in", "Log in to continue",
    "Sign in to continue", "You need to login",
]

_LOGGED_IN_TEXT_PATTERNS = [
    "退出登录", "退出", "注销", "logout", "log out", "sign out",
    "个人中心", "我的", "会员中心", "用户中心", "我的账户",
    "My Account", "My account", "Sign out", "Log out",
]


def _looks_like_login_page(url: str, html: str) -> bool:
    """判断 URL+HTML 是否还在登录页。"""
    if url and _LOGIN_URL_PATTERNS.search(url):
        return True
    if html and '<input type="password"' in html.lower():
        return True
    html_lower = (html or "").lower()
    for pattern in _LOGIN_TEXT_PATTERNS:
        if pattern.lower() in html_lower:
            return True
    return False


async def _infer_form_selectors(page) -> dict[str, str]:
    """启发式推断用户名/密码/提交按钮的 CSS selector。

    策略：
      1) 密码框：document.querySelector('input[type=password]')
      2) 用户名框：密码框所在 <form> 内第一个 input[type=text] 或 input[type=email]
      3) 提交按钮：form.querySelector('button[type=submit], input[type=submit], button:not([type])')
    推断不出来返回空串。
    """
    try:
        result = await page.evaluate("""() => {
            const pw = document.querySelector('input[type=password]');
            if (!pw) return null;
            const form = pw.closest('form') || document.querySelector('form');
            if (!form) return {password: 'input[type=password]', username: '', submit: ''};

            // 用户名框：form 内第一个 text/email input
            let userInput = form.querySelector('input[type=text], input[type=email], input[type=tel], input[name*=user], input[name*=account], input[name*=email], input[name*=phone], input[id*=user], input[id*=account], input[id*=email], input[id*=phone]');
            let usernameSel = '';
            if (userInput) {
                if (userInput.id) usernameSel = '#' + CSS.escape(userInput.id);
                else if (userInput.name) usernameSel = 'input[name="' + userInput.name + '"]';
                else usernameSel = 'input[type=' + (userInput.type || 'text') + ']';
            }

            // 提交按钮
            let submitBtn = form.querySelector('button[type=submit], input[type=submit], button[type=button], button:not([type])');
            let submitSel = '';
            if (submitBtn) {
                if (submitBtn.id) submitSel = '#' + CSS.escape(submitBtn.id);
                else if (submitBtn.className) submitSel = 'button.' + submitBtn.className.split(' ')[0];
                else submitSel = 'button[type=submit], input[type=submit]';
            }

            return {
                password: 'input[type=password]',
                username: usernameSel,
                submit: submitSel || 'button[type=submit], input[type=submit]'
            };
        }""")
        return result or {"password": "input[type=password]", "username": "", "submit": ""}
    except Exception:
        return {"password": "input[type=password]", "username": "", "submit": ""}


async def _looks_like_logged_in(page, success_indicator: str = "") -> bool:
    """判断是否已登录成功。

    判断条件（任一命中即视为成功）：
      1) success_indicator 给定的 selector 在页面存在
      2) 当前 URL 不再含 /login /signin 等
      3) 页面 HTML 含"退出登录"/"logout"等关键词
    """
    # 1) 显式 selector
    if success_indicator:
        try:
            el = await page.query_selector(success_indicator)
            if el:
                return True
        except Exception:
            pass

    # 2) URL 判断
    current_url = page.url or ""
    if current_url and not _LOGIN_URL_PATTERNS.search(current_url):
        # URL 不在登录页路径上 → 可能已登录
        # 再验证 HTML 不含登录表单
        try:
            html = await page.content()
            if not _looks_like_login_page(current_url, html):
                return True
        except Exception:
            # 拿不到 HTML 就只靠 URL 判断
            return True

    # 3) HTML 关键词
    try:
        html = await page.content()
        html_lower = html.lower()
        for pattern in _LOGGED_IN_TEXT_PATTERNS:
            if pattern.lower() in html_lower:
                return True
    except Exception:
        pass

    return False


async def _has_captcha_on_page(page) -> str:
    """检测页面是否有验证码/滑块/二步验证，返回检测到的类型或空串。"""
    try:
        html = await page.content()
    except Exception:
        return ""

    html_lower = html.lower()
    captcha_signals = [
        (r"geetest[_\-]?\w*|gt\.js|geetest\.com", "geetest 滑块"),
        (r"nocaptcha|aliyun.?captcha|nc_iconfont", "阿里云盾"),
        (r"tcaptcha|tcaptcha_iframe|captcha\.qq\.com", "腾讯防水墙"),
        (r"yidun|necaptcha|c\.dun\.163", "网易易盾"),
        (r"slider[_\- ]?captcha|drag[_\- ]?captcha|puzzle[_\- ]?captcha", "滑块验证码"),
        (r"滑动验证|拖动滑块|完成拼图|滑块验证", "滑块验证码"),
        (r"二步验证|两步验证|two.?factor|2fa|otp", "二步验证"),
        (r"captcha[_\- ]?image|图形验证码|img[_\- ]?captcha", "图形验证码"),
    ]
    for pattern, name in captcha_signals:
        if re.search(pattern, html_lower):
            return name
    return ""


async def _playwright_login(
    login_url: str,
    username: str,
    password: str,
    username_selector: str = "",
    password_selector: str = "",
    submit_selector: str = "",
    success_indicator: str = "",
    origin: str = "",
    headless: bool = True,
    wait_ms: int = 5000,
) -> tuple[str, str, list[dict], bool, str]:
    """跑 Playwright 登录流程。返回 (final_url, html, cookies, ok, reason)。"""
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless)
        context = await browser.new_context(
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/124.0.0.0 Safari/537.36")
        )
        page = await context.new_page()

        try:
            await page.goto(login_url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(2000)  # 等页面 JS 渲染

            # 推断或使用传入的 selector
            sel = {
                "username": username_selector,
                "password": password_selector,
                "submit": submit_selector,
            }
            if not sel["password"] or not sel["username"] or not sel["submit"]:
                inferred = await _infer_form_selectors(page)
                if not sel["password"]:
                    sel["password"] = inferred.get("password", "input[type=password]")
                if not sel["username"]:
                    sel["username"] = inferred.get("username", "")
                if not sel["submit"]:
                    sel["submit"] = inferred.get("submit", "")

            # 验证 selector 存在
            pw_el = await page.query_selector(sel["password"])
            if not pw_el:
                return ("", "", [], False, f"找不到密码输入框 (selector={sel['password']})")

            # 填表
            if sel["username"]:
                user_el = await page.query_selector(sel["username"])
                if not user_el:
                    return ("", "", [], False, f"找不到用户名输入框 (selector={sel['username']})")
                await user_el.fill(username)

            await pw_el.fill(password)

            # 提交
            if sel["submit"]:
                btn = await page.query_selector(sel["submit"])
                if btn:
                    await btn.click()
                else:
                    # 回车提交
                    await pw_el.press("Enter")
            else:
                await pw_el.press("Enter")

            # 等跳转
            await page.wait_for_timeout(wait_ms)

            # 检测验证码
            captcha_type = await _has_captcha_on_page(page)
            if captcha_type:
                cookies = await context.cookies()
                return (page.url, "", cookies, False,
                        f"LOGIN_NEEDS_MANUAL: 检测到{captcha_type}，无法自动登录。"
                        f"建议用 ask_user 让用户手动登录一次并把 Cookie 贴进来")

            # 获取最终状态
            final_url = page.url or ""
            html = await page.content()

            # 判断是否登录成功
            logged_in = await _looks_like_logged_in(page, success_indicator)

            # 提取 Cookie
            cookies = await context.cookies()

            return (final_url, html, cookies, logged_in,
                    "" if logged_in else "提交后仍在登录页，可能用户名密码错误或登录表单非标准")

        except Exception as e:
            return ("", "", [], False, f"Playwright 执行异常: {type(e).__name__}: {e}")
        finally:
            await browser.close()


def _cookies_to_string(cookies: list[dict]) -> str:
    """把 Playwright cookies list 转成 "k1=v1; k2=v2" 字串。"""
    parts = []
    for c in cookies:
        name = c.get("name", "")
        value = c.get("value", "")
        if name and value:
            parts.append(f"{name}={value}")
    return "; ".join(parts)


def _extract_origin(login_url: str) -> str:
    """从 login_url 提取 scheme+host 作为 origin。"""
    parsed = urlparse(login_url)
    if parsed.scheme and parsed.netloc:
        return f"{parsed.scheme}://{parsed.netloc}"
    return login_url.rstrip("/")


# ---------------------------------------------------------------------------
# @tool 工具
# ---------------------------------------------------------------------------

@tool
def login_site(login_url: str, username: str, password: str,
               username_selector: str = "",
               password_selector: str = "",
               submit_selector: str = "",
               success_indicator: str = "",
               origin: str = "",
               headless: bool = True,
               wait_ms: int = 5000) -> str:
    """用 Playwright 自动走表单登录流程，成功后把 Cookie 存进站点档案。

    适用场景：用户给了用户名密码但没给 Cookie，且站点登录表单相对标准
    （有 input[type=password] + 一个用户名输入框 + 一个提交按钮）。
    简单站如果用户已经能直接给 Cookie 字串，优先用 save_site_profile(origin, cookies=...)
    存档；只有"用户不想自己 F12 抓 Cookie"或"Cookie 有 HttpOnly 拿不到"时才用本工具。

    登录成功后会自动：
      1) 提取浏览器 context.cookies() 的所有 Cookie
      2) 拼成 "k1=v1; k2=v2" 字串
      3) 调用 upsert_site(origin, cookies=<字串>, notes="auto-login at <date>")
         存进 data/profiles/<domain>.yaml
      4) 下次抓取该站，工具会自动从档案读 Cookie（get_site_cookies），不再调本工具。

    selector 自动推断（不需要手填）：
      - 密码框：document.querySelector('input[type=password]')
      - 用户名框：密码框所在 <form> 内第一个 input[type=text] 或 input[type=email]
      - 提交按钮：form.querySelector('button[type=submit], input[type=submit], button:not([type])')
      推断失败才回退到用户提供的 username_selector/password_selector/submit_selector。

    登录成功判断（任一命中即视为成功）：
      1) 当前 URL 不再含 /login /signin /account/login（避免被重定向回登录页）
      2) success_indicator 给定的 selector 在页面存在
      3) 页面 HTML 含"退出登录"/"logout"/"个人中心"等关键词

    参数：
        login_url:           登录页 URL，例 "https://example.com/login"
        username:            登录用户名（手机号/邮箱/账号）
        password:            登录密码（只用于自动填表，不存档）
        username_selector:   可选，用户名输入框 CSS selector；留空自动推断
        password_selector:   可选，密码框 CSS selector；留空自动推断
        submit_selector:     可选，提交按钮 selector；留空自动推断
        success_indicator:   可选，登录成功标志 selector；留空靠 URL/关键词启发式判断
        origin:              可选，站点根 URL（用于存档）。留空自动从 login_url 提取 scheme+host
        headless:            是否无头；某些站会检测 headless，可设 False
        wait_ms:             点提交后等待跳转/页面渲染的毫秒数，默认 5000

    返回：
        成功："LOGIN_OK: <origin> 已登录，Cookie 已存档（N 条）。下次直接抓该站即可。"
        失败："LOGIN_FAIL: <原因>"
        需手动："LOGIN_NEEDS_MANUAL: 检测到验证码/滑块/二步验证，无法自动登录。建议用 ask_user ..."
    """
    if not login_url:
        return "ERROR login_site: login_url 不能为空"
    if not username or not password:
        return "ERROR login_site: username 和 password 不能为空"

    # 确定 origin
    site_origin = origin or _extract_origin(login_url)
    if not site_origin:
        return f"ERROR login_site: 无法从 login_url 提取 origin，请手动传 origin 参数"

    # 跑 Playwright
    try:
        final_url, html, cookies, ok, reason = asyncio.run(
            _playwright_login(
                login_url=login_url,
                username=username,
                password=password,
                username_selector=username_selector,
                password_selector=password_selector,
                submit_selector=submit_selector,
                success_indicator=success_indicator,
                origin=site_origin,
                headless=headless,
                wait_ms=wait_ms,
            )
        )
    except Exception as e:
        return f"LOGIN_FAIL: Playwright 执行异常: {type(e).__name__}: {e}"

    if not ok:
        # 如果检测到验证码等需要手动的情况，cookies 可能有部分值
        return reason if reason else "LOGIN_FAIL: 未知原因"

    # 登录成功 → 存 Cookie
    cookie_str = _cookies_to_string(cookies)
    if not cookie_str:
        return "LOGIN_FAIL: 登录成功但未提取到 Cookie（可能是 HttpOnly + sameSite 限制）"

    now = datetime.now().isoformat(timespec="seconds")
    try:
        upsert_site(
            origin=site_origin,
            title=site_origin,
            strategy="login_cookie",
            notes=f"auto-login at {now} via login_site",
            cookies=cookie_str,
        )
    except Exception as e:
        return f"LOGIN_OK but save failed: 登录成功但存档失败: {e}。Cookie: {cookie_str[:100]}..."

    return (f"LOGIN_OK: {site_origin} 已登录，Cookie 已存档（{len(cookies)} 条）。"
            f"下次直接抓该站即可。")


@tool
def check_login_status(origin: str, probe_url: str = "",
                       success_indicator: str = "") -> str:
    """检查某站点的登录态是否还有效（基于已存档的 Cookie）。

    适用场景：用户反馈"之前的登录失效了？"或"上次抓不到内容是不是 Cookie 过期了"
    时调它。先从 data/profiles/<domain>.yaml 读出已存的 Cookie，再请求一个
    需登录才能看的页面，看是否被重定向到登录页 / 出现登录表单。

    探测策略（不需要 Agent 手动给 URL 也能跑）：
      - probe_url 留空时，默认探测 origin + /account / /user / /profile / /me / /member
        这几个常见需登录路径（依次试，第一个非 404 就用）。
      - success_indicator 给定 selector：页面里命中即视为"已登录"。
      - 都不填：判断条件是 URL 未被重定向到 /login /signin，且 HTML 不含
        <input type="password" 或"请登录"等关键词。

    参数：
        origin:            站点根 URL，例 "https://weread.qq.com"
        probe_url:         可选，指定要探测的具体 URL（如某本书的目录页）；
                           留空自动尝试常见需登录路径
        success_indicator: 可选，登录态有效时页面应存在的元素 selector

    返回：
        "LOGGED_IN: Cookie 仍然有效（探测 <probe_url>，未跳登录页）"
        "LOGGED_OUT: Cookie 已失效（被重定向到 <login_url> / 出现登录表单）"
        "NO_COOKIE: 站点档案未存 Cookie，请先 login_site 或 save_site_profile 存档"
        "ERROR check_login_status: <原因>"
    """
    if not origin:
        return "ERROR check_login_status: origin 不能为空"

    norm_origin = _normalize_origin(origin) or origin
    cookie_str = get_site_cookies(norm_origin)
    if not cookie_str:
        return "NO_COOKIE: 站点档案未存 Cookie，请先 login_site 或 save_site_profile 存档"

    headers = {
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/124.0.0.0 Safari/537.36"),
        "Cookie": cookie_str,
    }

    # 确定 probe URL
    probe_urls = []
    if probe_url:
        probe_urls.append(probe_url)
    else:
        # 自动尝试常见需登录路径
        for path in ["/account", "/user", "/profile", "/me", "/member",
                     "/user/profile", "/account/profile"]:
            probe_urls.append(f"{norm_origin.rstrip('/')}{path}")

    for purl in probe_urls:
        try:
            resp = requests.get(purl, headers=headers, allow_redirects=True,
                                timeout=15)
        except requests.RequestException as e:
            continue

        # 检查是否被重定向到登录页
        final_url = resp.url or purl
        html = resp.text or ""

        if _looks_like_login_page(final_url, html):
            return (f"LOGGED_OUT: Cookie 已失效（被重定向到 {final_url} / 出现登录表单）。"
                    f"建议重新 login_site 或让用户手动提供新 Cookie")

        # 检查 success_indicator
        if success_indicator:
            # 简单检查 selector 是否在 HTML 里（不需要 BeautifulSoup，用字串匹配）
            # 从 selector 提取关键属性
            if success_indicator in html:
                return f"LOGGED_IN: Cookie 仍然有效（探测 {purl}，命中 {success_indicator}）"

        # 检查是否有登录表单
        if '<input type="password"' in html.lower():
            return f"LOGGED_OUT: Cookie 已失效（{purl} 出现登录表单）"

        # 检查是否有"请登录"等关键词
        html_lower = html.lower()
        for pattern in _LOGIN_TEXT_PATTERNS:
            if pattern.lower() in html_lower:
                return f'LOGGED_OUT: Cookie 已失效（{purl} 含"{pattern}"提示）'

        # 非 404 且未跳登录页 → 认为有效
        if resp.status_code < 400:
            return f"LOGGED_IN: Cookie 仍然有效（探测 {purl}，状态 {resp.status_code}，未跳登录页）"

    return ("ERROR check_login_status: 所有探测路径均失败（404/超时），"
            "请手动指定 probe_url 参数")
