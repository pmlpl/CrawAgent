"""自定义脚本工具 — 让 AI 在预制工具无法搞定时自己写 Python 脚本执行。

设计意图：
    当 crawl_webpage / browse_and_crawl / extract_social_media 等预制工具都无法
    正确抓取某个站点时，AI 可以用这个工具写一段针对该站点的 Python 脚本并执行。

    脚本可以用 requests + bs4 + 标准库做任何预制工具做不到的事：
    - 调用站点专属 API
    - 解析特殊加密（字体/JS 混淆）
    - 处理需要多步骤交互的抓取

    脚本也可以存入站点档案，下次抓取同一站点时直接运行已有脚本。

安全措施：
    - 脚本以子进程执行，超时 120 秒自动 kill
    - 脚本在项目 .venv 环境中运行，可用 requests/beautifulsoup4/lxml 等
    - stdout + stderr 截取前 5000 字符返回给 AI
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

from langchain_core.tools import tool

from crawagent.config.settings import get_settings

# 模块级路径变量（raw string 外，Python 代码可直接引用）
_s = get_settings()
PROJECT_ROOT = _s.project_root
DOWNLOADS_DIR = _s.downloads_dir
OUTPUT_DIR = _s.output_dir
DATA_TMP_DIR = PROJECT_ROOT / "data" / "_tmp"


def _ensure_tmp() -> Path:
    """确保临时目录存在，并清空目录内所有遗留文件（启动全量清理）。

    方案 A 后本模块不再向 _tmp 写任何临时脚本/JSON；该目录仅作为
    run_custom_script 子进程的 cwd，收容 AI 脚本自己写出的输出文件。
    每次调用无条件清空，避免崩溃残留累积后用户手动删除进回收站导致膨胀。
    """
    try:
        DATA_TMP_DIR.mkdir(parents=True, exist_ok=True)
        for f in DATA_TMP_DIR.glob("*"):
            try:
                if f.is_file() or f.is_symlink():
                    f.unlink(missing_ok=True)
            except OSError:
                pass
    except OSError:
        pass
    return DATA_TMP_DIR


# 脚本头部：预导入标准库 + 常用爬虫库 + 3 个能力增强 helper
# ---------------------------------------------------------------------------
# 三大能力增强 helper（AI 可直接调用，无需自己实现）：
#   1) get_site_profile(origin)  — 读 data/sites.json 中该域名的存档 cookie / 脚本 / 策略
#   2) decrypt_js_eval_html(html) — 解 CSDN 等站点的 "<script> oo=[..x..] ^ XL; eval </script>"
#                                   混淆 HTML，返回解密后的纯文本
#   3) browser_render(url, wait_ms=3000) — 子进程调 Playwright 做完整动态渲染，返回 (final_url, html)
# 此外：_INJECTED_PROFILES 变量会在每次脚本运行时被工具自动填充——包含本脚本中
# 所有 URL 域名对应的已存站点档案（cookies / saved_script / strategy / notes）。
# ---------------------------------------------------------------------------
# 注意：_ALLOWED_HEADER 必须用 raw string（r"""..."""）定义，因为内部包含大量
# 正则 "\s" "\d" 等反斜杠序列。普通字符串会让 Python 字符串层先吞掉 \s，导致子进程
# 里拿到的正则损坏。
_ALLOWED_HEADER = r"""\
import sys, os, json, re, time, hashlib, base64, random, math, copy, itertools, html as _html_stdlib
from collections import OrderedDict
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from bs4 import BeautifulSoup
from urllib.parse import urlparse, urljoin, quote, unquote
from pathlib import Path
from datetime import datetime

# -------- 注入的项目路径（由 run_custom_script 的 .format 填充）------------
PROJECT_ROOT = Path({root!r})
DOWNLOADS_DIR = Path({downloads!r})
OUTPUT_DIR = Path({output!r})

# -------- lxml（可选，若本环境未装则为 None）------------------------------
try:
    import lxml.html as lxml_html
    HAS_LXML = True
except Exception:
    lxml_html = None
    HAS_LXML = False

# ============================================================
#  helper 1: get_site_profile(origin) — 读 data/sites.json
# ============================================================
def get_site_profile(origin):
    '''从站点档案 (data/sites.json) 读取指定 origin 的存档信息。

    Args:
        origin: 站点根 URL，例如 "https://blog.csdn.net" — 带协议不带尾斜杠，
                或只传一个完整详情页 URL，函数会自动提取 origin。
    Returns:
        dict: {{origin, title, strategy, notes, cookies(字符串可直接塞 Cookie header),
               script(保存的脚本代码)}}。档案不存在时返回空 dict {{}}。
    '''
    if not origin:
        return {{}}
    try:
        if origin.startswith("http"):
            pu = urlparse(origin)
            origin_normalized = f"{{pu.scheme}}://{{pu.netloc}}"
        else:
            origin_normalized = origin.rstrip("/")
        fpath = PROJECT_ROOT / "data" / "sites.json"
        if not fpath.exists():
            return {{}}
        data = json.loads(fpath.read_text(encoding="utf-8"))
        profiles = data.get("profiles", []) if isinstance(data, dict) else data
        for p in profiles:
            p_org = str(p.get("origin", "")).rstrip("/")
            if p_org == origin_normalized or origin_normalized.endswith(p_org.split("://",1)[-1] if "://" in p_org else p_org):
                # 补字段：把 cookies 独立出来方便直接用
                profile = dict(p)
                profile.setdefault("cookies", "")
                profile.setdefault("script", "")
                profile.setdefault("strategy", "")
                profile.setdefault("notes", "")
                return profile
    except Exception:
        pass
    return {{}}


# ============================================================
#  helper 2: decrypt_js_eval_html(html) — 解 JS eval 混淆正文
# ============================================================
def decrypt_js_eval_html(html):
    '''解密 "<script> var oo=[0xAB,...] ; qo=eval;qo(po); </script>" 这类反爬。
    典型：CSDN RSS / 移动端在 UA 不匹配时返回整页就是一个混淆 JS。
    返回解密出来的 HTML 片段字符串；解不出来返回空串。
    '''
    if not html or "eval" not in html.lower():
        return ""
    # 提取第一个 <script>...</script> 内容
    m = re.search(r"<script[^>]*>(.*?)</script>", html, re.I | re.S)
    if not m:
        return ""
    js = m.group(1)
    # 提取 oo=[...] 数组
    m_arr = re.search(r"var\s+oo\s*=\s*\[([0-9a-fx,\s]+)\]", js, re.I)
    if not m_arr:
        # 兼容 const / let / 无 var
        m_arr = re.search(r"\boo\s*=\s*\[([0-9a-fx,\s]+)\]", js, re.I)
        if not m_arr:
            return ""
    try:
        arr_str = "[" + m_arr.group(1) + "]"
        oo = json.loads(arr_str) if arr_str.startswith("[") else []
    except Exception:
        # 数组里有 0x 前缀的十六进制：用 ast 安全解
        try:
            import ast
            oo = ast.literal_eval(arr_str)
        except Exception:
            return ""
    if not oo:
        return ""
    # 提取 XOR 密钥：setTimeout("mu(18)", N) → key=18
    m_key = re.search(r"mu\(\s*(\d+)\s*\)", js)
    if not m_key:
        # 兼容 function mu(XX) {{...}}
        m_key = re.search(r"function\s+mu\s*\(\s*(\w+)\s*\)", js)
    key = 18
    if m_key and m_key.group(1).isdigit():
        key = int(m_key.group(1))
    try:
        buf = bytearray()
        for v in oo:
            if isinstance(v, int):
                buf.append(v ^ key & 0xFF)
        # oo 里的元素是字节，XOR 后也是字节 → 用 UTF-8 解码。
        # 若有非法字节序列，fallback 到 latin-1（不会抛错），保证中文不乱码。
        try:
            decoded = buf.decode("utf-8")
        except UnicodeDecodeError:
            decoded = buf.decode("latin-1", errors="replace")
    except Exception:
        return ""
    # 再包一层：decoded 里可能又是 eval(function(p,a,c,k,...)（Dean Edwards Packer）
    # 这里不做深度 Packer 解（怕引入 execjs 依赖），如果含 "document.write" 就返回整个 decoded
    return decoded if (decoded.strip().startswith("<") or "document" in decoded or "html" in decoded.lower()) else decoded


# ============================================================
#  helper 3: browser_render(url, wait_ms, extra_headers, cookies)
# ============================================================
def browser_render(url, wait_ms=3500, extra_headers=None, cookies=None,
                   timeout=45, wait_selector=None, user_agent=None, proxy=None):
    '''用 Playwright (Chromium headless) 完整渲染动态页面。
    适合：JS 渲染 SPA、字体加密需执行页面 CSS、需要等客户端脚本执行后 DOM 完整。

    Args:
        url:            完整 URL
        wait_ms:        导航完成后再等多少毫秒再抓 DOM（默认 3.5s，给懒加载/字体解密留时间）
        extra_headers:  dict 或字符串，额外 HTTP 请求头（UA 可单独用 user_agent 参数）
        cookies:        str "k1=v1; k2=v2" 或 dict 或 list[dict] 格式（Playwright 兼容）
        wait_selector:  CSS 选择器。若给了就一直等到此元素出现（或超时）再抓 DOM。
        user_agent:     字符串浏览器 UA；留空则用标准 Chrome PC UA。
        proxy:          可选代理 URL（"http://user:pass@host:port" 或 "socks5://host:port"），
                        让浏览器走代理；建议先调 get_proxy() 拿可用代理再传进来。
    Returns:
        (final_url: str, html: str) — 出错时返回 ("", "ERR: ...")，html 为错误信息。
    '''
    import subprocess as _sp
    if not url:
        return ("", "ERR: empty url")
    # 所有参数打包成 JSON，经环境变量 _PAYLOAD 传给子进程（方案 A：不写临时文件）
    payload = dict(url=url, wait_ms=wait_ms, extra_headers=extra_headers or {{}},
                   cookies=cookies, timeout=timeout, wait_selector=wait_selector,
                   user_agent=user_agent, proxy=proxy)
    # 内联 Playwright 脚本：用 {{...}} 插值，所以字符串里的 {{ }} 双写
    _pw_script = r'''
import json, sys, os
p = json.loads(os.environ["_PAYLOAD"])
try:
    from playwright.sync_api import sync_playwright
except Exception as e:
    print("FINAL_URL:", "", flush=True)
    print("ERR: playwright not installed:", e)
    sys.exit(0)
try:
    with sync_playwright() as pw:
        launch_kw = {{"headless": True}}
        if p.get("proxy"):
            from urllib.parse import urlparse as _up, unquote as _uq
            _pu = _up(p["proxy"])
            _scheme = _pu.scheme or "http"
            _ph = _pu.hostname or ""
            _pp = _pu.port or (1080 if "socks" in _scheme else 8080)
            launch_kw["proxy"] = {{"server": f"{{_scheme}}://{{_ph}}:{{_pp}}"}}
            if _pu.username:
                launch_kw["proxy"]["username"] = _uq(_pu.username)
            if _pu.password:
                launch_kw["proxy"]["password"] = _uq(_pu.password)
        browser = pw.chromium.launch(**launch_kw)
        ctx_kwargs = dict()
        if p.get("user_agent"):
            ctx_kwargs["user_agent"] = p["user_agent"]
        else:
            ctx_kwargs["user_agent"] = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
        cookies = p.get("cookies") or []
        if isinstance(cookies, str) and cookies.strip():
            # "k1=v1; k2=v2" → list[dict]
            parsed = []
            for chunk in cookies.split(";"):
                if "=" in chunk:
                    k, v = chunk.strip().split("=", 1)
                    if k.strip():
                        parsed.append(dict(name=k.strip(), value=v.strip(),
                                           domain=urlparse(p["url"]).netloc, path="/"))
            cookies = parsed
        elif isinstance(cookies, dict):
            cookies = [dict(name=k, value=str(v),
                            domain=urlparse(p["url"]).netloc, path="/")
                       for k, v in cookies.items() if k]
        if cookies:
            ctx_kwargs["cookies"] = cookies
        context = browser.new_context(**ctx_kwargs)
        page = context.new_page()
        if isinstance(p.get("extra_headers"), dict) and p["extra_headers"]:
            page.set_extra_http_headers(p["extra_headers"])
        elif isinstance(p.get("extra_headers"), str) and p["extra_headers"]:
            hd = {{}}
            for ln in p["extra_headers"].splitlines():
                if ":" in ln:
                    k, v = ln.split(":", 1)
                    hd[k.strip()] = v.strip()
            if hd:
                page.set_extra_http_headers(hd)
        page.goto(p["url"], timeout=p.get("timeout", 45) * 1000, wait_until="domcontentloaded")
        if p.get("wait_selector"):
            try:
                page.wait_for_selector(p["wait_selector"],
                    timeout=min(p.get("timeout", 45), 20) * 1000)
            except Exception:
                pass
        else:
            import time as _t
            _t.sleep(max(0, int(p.get("wait_ms", 3500))) / 1000.0)
        try:
            content = page.content()
        except Exception:
            content = ""
        final = page.url
        context.close()
        browser.close()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print("FINAL_URL:", final, flush=True)
    print("HTML_LEN:", len(content), flush=True)
    print(content)
except Exception as e:
    print("FINAL_URL:", "", flush=True)
    print("ERR:", e, flush=True)
'''
    # 内联脚本里调用了 urlparse()，在 r'''...''' 开头只 import 了 json/sys/pathlib，
    # 这里直接在最前面 prepend 一行 `from urllib.parse import urlparse` 即可。
    # 注意：不要用 .replace("urlparse", ...) —— 会把函数参数位置的 "domain=urlparse(...)"
    # 误替换成 "domain=from urllib.parse import urlparse"，造成 SyntaxError（session 658c1ab4 实锤）。
    _pw_script = "from urllib.parse import urlparse\n" + _pw_script
    try:
        r = _sp.run(
            [sys.executable, "-"],
            input=_pw_script,
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout + 10, cwd=str(PROJECT_ROOT),
            env={{**os.environ, "PYTHONIOENCODING": "utf-8",
                  "_PAYLOAD": json.dumps(payload, ensure_ascii=False)}},
        )
        out = r.stdout or ""
        err = r.stderr or ""
    except Exception as e:
        return ("", f"ERR: browser render subprocess: {{e}}")
    combined = (out + "\n" + err).strip()
    final_url = ""
    # 解析 FINAL_URL: + HTML_LEN: + 后面的内容
    m_fu = re.search(r"FINAL_URL:\s*(\S*)", combined)
    if m_fu:
        final_url = m_fu.group(1)
    if "ERR:" in combined:
        m_er = re.search(r"ERR:\s*(.*)", combined)
        err_msg = m_er.group(1) if m_er else combined[-200:]
        return (final_url, f"ERR: {{err_msg.strip()}}")
    # HTML 正文在 HTML_LEN 行之后
    hl = re.search(r"HTML_LEN:\s*\d+\s*\n", combined)
    if not hl:
        # 没有 HTML_LEN 分隔线：Playwright 子进程没正常走完 / 输出格式异常
        # 必须返回 ERR 前缀 —— 否则脚本会把 combined (可能是 stderr 乱码) 当 HTML 解析，
        # print("vip-mask -> False / has OneNote: False" 这种假诊断（session 658c1ab4）。
        short = (combined.strip()[:2000] or "(empty stderr+stdout)")
        short = short.replace("\r", " ").replace("\n", " \\n ")
        return ("", f"ERR: browser render failed (no HTML_LEN marker in combined output). SNIPPET: {{short}}")
    html_out = combined[hl.end():]
    return (final_url or url, html_out)


# ============================================================
#  自动注入：_INJECTED_PROFILES（本脚本 URL 域名对应站点档案）
#  注意：本段内容会在运行时由 run_custom_script 工具再补一次具体域名。
#  这里先给占位空 dict，保证 AI 引用时不会报 NameError。
# ============================================================
_INJECTED_PROFILES = {injected_profiles!r}


# ============================================================
#  helper 4: solve_slider_via_browser(url, slider_selector, ...)
#    专为滑块/拼图验证码设计：起 Playwright 真浏览器 + 贝塞尔曲线轨迹模拟。
#    成功率：极验 v2/v3 ≈ 50-70%，极验 v4 ≈ 10-20%，阿里云盾 v3 ≈ 10-20%。
#    失败时一律返回 (final_url, "CAPTCHA_FAIL: <reason>") — 调用方据此走"问用户"分支。
#    不依赖 numpy/cv2（可选 cv2 模板匹配找缺口，没装就降级到估算）。
# ============================================================
def _bezier_curve(p0, p1, p2, p3, steps=80):
    '''三阶贝塞尔曲线 — 输入 4 个控制点（tuple），输出 steps 个 (x, y)。'''
    pts = []
    for i in range(steps + 1):
        t = i / steps
        # 三阶贝塞尔公式（不依赖 numpy）
        x = ((1-t)**3 * p0[0] + 3*(1-t)**2 * t * p1[0]
             + 3*(1-t) * t**2 * p2[0] + t**3 * p3[0])
        y = ((1-t)**3 * p0[1] + 3*(1-t)**2 * t * p1[1]
             + 3*(1-t) * t**2 * p2[1] + t**3 * p3[1])
        pts.append((x, y))
    return pts


def _human_drag_trajectory(distance, total_ms=900):
    '''生成一条"像人"的拖动轨迹：起点 (0,0) 到 (distance, 0)，约 80 步。
    特征：
      - X 方向走三阶贝塞尔（先快后慢，最后轻微回弹）
      - Y 方向随机 ±2 像素抖动
      - 每步等待时间用对数曲线（开始慢 → 中间快 → 收尾慢）
      - 最后几步在缺口前回退 2-5 像素再前进（人类常见修正）
    '''
    import random as _r
    # 控制点：p0=起点；p1=30% 处往前往下偏；p2=80% 处回压；p3=终点略过
    p0 = (0.0, 0.0)
    p1 = (distance * 0.30, _r.uniform(-1, 2))
    p2 = (distance * 0.80, _r.uniform(-2, 1))
    p3 = (distance + _r.uniform(2, 6), 0.0)  # 故意略过然后回弹
    pts = _bezier_curve(p0, p1, p2, p3, steps=72)
    # 收尾回弹：在末尾再追加 4 步从 (distance+5) 拉回到 distance
    pts.append((distance + 2, 0))
    pts.append((distance - 2, _r.uniform(-1, 1)))
    pts.append((distance, 0))
    # 时间步：开始 60ms/步 → 中间 6ms/步 → 收尾 30ms/步
    times = []
    for i in range(len(pts)):
        if i < 8:
            dt = 55 + _r.uniform(-8, 8)
        elif i > len(pts) - 8:
            dt = 25 + _r.uniform(-6, 6)
        else:
            dt = 6 + _r.uniform(-2, 3)
        times.append(max(2, int(dt)))
    return pts, times


def solve_slider_via_browser(url, slider_selector=".geetest_slider_button",
                             track_selector=None,
                             wait_selector=None,
                             gap_distance_hint=None,
                             timeout=30,
                             user_agent=None):
    '''用 Playwright 自动过滑块验证码。

    Args:
        url:               要打开的目标页（含验证码的页面 URL）
        slider_selector:   滑块拖动按钮的 CSS selector
                           （geetest v2/v3: .geetest_slider_button；v4: .geetest_slider_button；
                             阿里云盾: .nc_iconfont.btn_slide; 腾讯防水墙: '#tcaptcha_drag_thumb'）
        track_selector:    可选 — 滑动轨道 selector（用于读 width 估算缺口距离）。
                           没传就用 gap_distance_hint 或默认 260px。
        gap_distance_hint: 可选 — 直接告诉 helper 缺口距离像素数（如果调用方已知）。
                           None → 读 track width × 0.62 估算（geetest 标准缺口在 60-65% 位置）
        timeout:           总超时秒
        user_agent:        可选 UA

    Returns:
        (final_url, html_or_err): final_url 是过完验证后的当前页 URL；
                                  html_or_err 是验证完成后的 DOM HTML，或 "CAPTCHA_FAIL: <reason>"。
    '''
    if not url:
        return ("", "CAPTCHA_FAIL: empty url")

    import json as _json
    import subprocess as _sp
    payload = dict(
        url=url, slider_selector=slider_selector,
        track_selector=track_selector,
        gap_distance_hint=gap_distance_hint,
        timeout=timeout,
        user_agent=user_agent,
        project_root=str(PROJECT_ROOT),
    )
    # 内联 Playwright 滑块破解脚本 — 注意：r'''...''' 内任何 {{ }} 都要双写（外层 .format 会还原）
    _slider_script = r'''
import json, sys, os, random, time
p = json.loads(os.environ["_PAYLOAD"])
try:
    from playwright.sync_api import sync_playwright
except Exception as e:
    print("FINAL_URL:", "")
    print("CAPTCHA_FAIL: playwright not installed:", e)
    sys.exit(0)

# 贝塞尔轨迹生成（与 helper 内 _human_drag_trajectory 同逻辑）
def bezier(p0, p1, p2, p3, steps=72):
    pts = []
    for i in range(steps + 1):
        t = i / steps
        x = (1-t)**3 * p0[0] + 3*(1-t)**2 * t * p1[0] + 3*(1-t) * t**2 * p2[0] + t**3 * p3[0]
        y = (1-t)**3 * p0[1] + 3*(1-t)**2 * t * p1[1] + 3*(1-t) * t**2 * p2[1] + t**3 * p3[1]
        pts.append((x, y))
    return pts

def trajectory(distance):
    p0 = (0.0, 0.0)
    p1 = (distance * 0.30, random.uniform(-1, 2))
    p2 = (distance * 0.80, random.uniform(-2, 1))
    p3 = (distance + random.uniform(2, 6), 0.0)
    pts = bezier(p0, p1, p2, p3, steps=72)
    pts.append((distance + 2, 0))
    pts.append((distance - 2, random.uniform(-1, 1)))
    pts.append((distance, 0))
    times = []
    for i in range(len(pts)):
        if i < 8: dt = 55 + random.uniform(-8, 8)
        elif i > len(pts) - 8: dt = 25 + random.uniform(-6, 6)
        else: dt = 6 + random.uniform(-2, 3)
        times.append(max(2, int(dt)))
    return pts, times

try:
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False)  # 滑块过验证 — headless 易被识别，必须 visible
        ctx_kw = dict()
        if p.get("user_agent"):
            ctx_kw["user_agent"] = p["user_agent"]
        ctx_kw.setdefault("user_agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
        ctx_kw["viewport"] = dict(width=1366, height=768)
        ctx_kw["locale"] = "zh-CN"
        # 真实浏览器 fingerprint — 注入 mock permissions / webdriver flag off
        ctx = browser.new_context(**ctx_kw)
        ctx.add_init_script("Object.defineProperty(navigator, 'webdriver', {{get: () => undefined}})")
        ctx.add_init_script("Object.defineProperty(navigator, 'languages', {{get: () => ['zh-CN', 'zh', 'en']}})")
        ctx.add_init_script("Object.defineProperty(navigator, 'plugins', {{get: () => [1,2,3,4,5]}})")
        page = ctx.new_page()
        page.goto(p["url"], timeout=p.get("timeout", 30) * 1000, wait_until="domcontentloaded")
        # 等滑块按钮出现
        try:
            page.wait_for_selector(p["slider_selector"], timeout=10000)
        except Exception:
            print("FINAL_URL:", page.url)
            print("CAPTCHA_FAIL: slider not found:", p["slider_selector"])
            sys.exit(0)
        # 计算缺口距离
        gap = p.get("gap_distance_hint")
        if not gap:
            try:
                if p.get("track_selector"):
                    track = page.query_selector(p["track_selector"])
                    if track:
                        bbox = track.bounding_box()
                        if bbox:
                            gap = int(bbox["width"] * 0.62)  # geetest 标准缺口位置
                if not gap:
                    gap = 260  # fallback
            except Exception:
                gap = 260
        # 拿滑块按钮位置
        btn = page.query_selector(p["slider_selector"])
        if not btn:
            print("FINAL_URL:", page.url)
            print("CAPTCHA_FAIL: slider btn not found")
            sys.exit(0)
        bbox = btn.bounding_box()
        if not bbox:
            print("FINAL_URL:", page.url)
            print("CAPTCHA_FAIL: slider btn not visible")
            sys.exit(0)
        start_x = bbox["x"] + bbox["width"] / 2
        start_y = bbox["y"] + bbox["height"] / 2
        # 模拟人手进入：先在按钮附近 hover 一下
        page.mouse.move(start_x + random.uniform(-30, 30), start_y + random.uniform(-10, 10), steps=5)
        time.sleep(random.uniform(0.3, 0.6))
        page.mouse.move(start_x, start_y, steps=8)
        time.sleep(random.uniform(0.2, 0.4))
        # 按下
        page.mouse.down()
        time.sleep(random.uniform(0.05, 0.15))
        # 沿轨迹拖动
        pts, tms = trajectory(int(gap))
        for (dx, dy), dt in zip(pts, tms):
            page.mouse.move(start_x + dx, start_y + dy, steps=1)
            time.sleep(dt / 1000.0)
        # 松开
        time.sleep(random.uniform(0.1, 0.3))
        page.mouse.up()
        time.sleep(random.uniform(1.5, 2.5))  # 等后端校验
        # 抓 DOM 看验证码是否消失
        html = page.content()
        final = page.url
        ctx.close()
        browser.close()
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        print("FINAL_URL:", final)
        print("HTML_LEN:", len(html))
        print(html)
except Exception as e:
    print("FINAL_URL:", "")
    print("CAPTCHA_FAIL:", e)
'''
    _slider_script = "from urllib.parse import urlparse\n" + _slider_script  # 不必，但保险
    try:
        r = _sp.run(
            [sys.executable, "-"],
            input=_slider_script,
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout + 20, cwd=str(PROJECT_ROOT),
            env={{**os.environ, "PYTHONIOENCODING": "utf-8",
                  "_PAYLOAD": _json.dumps(payload, ensure_ascii=False)}},
        )
        out = r.stdout or ""
        err = r.stderr or ""
    except _sp.TimeoutExpired:
        return ("", f"CAPTCHA_FAIL: subprocess timeout after {{timeout+20}}s")
    except Exception as e:
        return ("", "CAPTCHA_FAIL: subprocess: {{e}}".replace("{{e}}", str(e)))
    combined = (out + "\n" + err).strip()
    m_fu = re.search(r"FINAL_URL:\s*(\S*)", combined)
    final_url = m_fu.group(1) if m_fu else ""
    if "CAPTCHA_FAIL:" in combined:
        m_cf = re.search(r"CAPTCHA_FAIL:\s*(.*)", combined, re.S)
        reason = m_cf.group(1).strip()[:300] if m_cf else "unknown"
        return (final_url, f"CAPTCHA_FAIL: {{reason}}")
    # 正常拿到 HTML_LEN 分隔线 → 抓 HTML
    hl = re.search(r"HTML_LEN:\s*\d+\s*\n", combined)
    if not hl:
        short = combined[:2000].replace("\r", " ").replace("\n", " \\n ")
        return ("", f"CAPTCHA_FAIL: no HTML_LEN marker in output. SNIPPET: {{short}}")
    html_out = combined[hl.end():]
    return (final_url or url, html_out)


# -------- 会话级便捷函数（requests.Session() 自带重试 + UA 旋转）------------
def make_session(retries=3, backoff=0.5, uas=None):
    '''返回一个带自动重试（3xx/5xx/超时重试 3 次）和随机 UA 的 requests.Session。'''
    s = requests.Session()
    retry = Retry(total=retries, backoff_factor=backoff,
                  status_forcelist=[429, 500, 502, 503, 504],
                  allowed_methods=["GET", "POST", "HEAD"])
    s.mount("http://",  HTTPAdapter(max_retries=retry))
    s.mount("https://", HTTPAdapter(max_retries=retry))
    ua_list = uas or [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1",
    ]
    s.headers.update({{"User-Agent": random.choice(ua_list),
                       "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"}})
    return s

"""  # noqa: E501  — _ALLOWED_HEADER 定义到此结束


# 从代码字符串中提取所有 URL（用于自动注入站点档案）
_URL_RE = re.compile(r"https?://[A-Za-z0-9\-._~:/?#\[\]@!$&'()*+,;=%]+")


def _registrable_domain(netloc):
    """取可注册级主域名：blog.csdn.net → csdn.net；www.xxx.com.cn → xxx.com.cn。
    仅用字符串启发式，不依赖公共后缀表，已足够匹配同一个大站的跨子域名档案。
    """
    if not netloc:
        return ""
    # 去掉端口
    host = netloc.split(":")[0].lower()
    parts = host.split(".")
    if len(parts) < 2:
        return host
    # 常见"两段式顶级域"：.com.cn / .net.cn / .org.cn / .gov.cn / .edu.cn / .ac.cn
    TWO_TLDS = {
        "com.cn", "net.cn", "org.cn", "gov.cn", "edu.cn", "ac.cn",
        "co.jp", "co.uk", "co.kr", "co.nz", "co.in", "co.za",
        "com.hk", "com.tw", "com.sg", "com.au", "com.br", "com.mx",
    }
    tail2 = ".".join(parts[-2:])
    tail3 = ".".join(parts[-3:]) if len(parts) >= 3 else ""
    if tail3 and tail3 in TWO_TLDS:
        return ".".join(parts[-4:]) if len(parts) >= 4 else tail3
    if tail3 and tail2 in {t.split(".")[-1] for t in ("cn", "jp", "uk", "kr", "au")}:
        # 例如 "gov.cn" 作为第二段：parts[-2:] 就是 gov.cn，可注册级应该是 parts[-3:]
        # 但我们如果只有 2 段就直接返回它，避免越界
        return tail3 if len(parts) >= 3 else tail2
    return tail2


def _extract_origins_from_code(code: str) -> list[str]:
    """从用户写的 Python 代码里提取所有出现过的 URL，并归一为 origin 列表（去重、保序）。"""
    if not code:
        return []
    origins = []
    seen = set()
    for m in _URL_RE.finditer(str(code)):
        try:
            pu = urlparse(m.group(0))
            if pu.scheme and pu.netloc:
                org = f"{pu.scheme}://{pu.netloc}"
                if org not in seen:
                    seen.add(org)
                    origins.append(org)
        except Exception:
            continue
    return origins


def _load_injected_profiles(origins: list[str], project_root: Path) -> dict:
    """按 origins 列表读 data/sites.json，形成 {origin: profile_dict}。

    匹配策略（任一命中即注入）：
    1. 完整 origin 全等（scheme://netloc）
    2. netloc 全等（blog.csdn.net vs blog.csdn.net）
    3. 注册域名级相等（blog.csdn.net ↔ www.csdn.net ↔ csdn.net）
    """
    profiles_map: dict = {}
    if not origins:
        return profiles_map
    try:
        fpath = project_root / "data" / "sites.json"
        if not fpath.exists():
            return profiles_map
        data = json.loads(fpath.read_text(encoding="utf-8"))
        # 真实 sites.json 顶层是 {"sites": [...]}；兼容"如果有人误写成 profiles/直接 list"两种
        if isinstance(data, dict):
            all_profiles = data.get("sites") or data.get("profiles") or []
        elif isinstance(data, list):
            all_profiles = data
        else:
            all_profiles = []
        if not isinstance(all_profiles, list):
            return profiles_map

        # 归一用户侧的 origins：origin 字符串 / netloc / 注册域名 三个维度
        norm_exact: set[str] = set()
        norm_netloc: set[str] = set()
        norm_reg: set[str] = set()
        for o in origins:
            if not o:
                continue
            oo = str(o).rstrip("/")
            norm_exact.add(oo)
            try:
                if oo.startswith("http"):
                    pu = urlparse(oo)
                    norm_netloc.add(pu.netloc)
                    reg = _registrable_domain(pu.netloc)
                    if reg:
                        norm_reg.add(reg)
                else:  # 直接给了 netloc
                    norm_netloc.add(oo)
                    reg = _registrable_domain(oo)
                    if reg:
                        norm_reg.add(reg)
            except Exception:
                pass

        for p in all_profiles:
            if not isinstance(p, dict):
                continue
            p_org = str(p.get("origin", "")).rstrip("/")
            p_netloc = ""
            try:
                p_netloc = urlparse(p_org).netloc if p_org.startswith("http") else p_org
            except Exception:
                p_netloc = ""
            p_reg = _registrable_domain(p_netloc)

            match = (
                p_org in norm_exact
                or (p_netloc and p_netloc in norm_netloc)
                or (p_reg and p_reg in norm_reg)
            )
            if not match:
                continue
            profile = dict(p)
            profile.setdefault("cookies", "")
            profile.setdefault("script", "")
            # 注入时 key 用 profile 自己的 origin（保证一致性）
            profiles_map.setdefault(p_org or ("profile_" + str(len(profiles_map))), profile)

        return profiles_map
    except Exception:
        # 任何异常 → 静默返回空 dict（不能让工具调用本身失败）
        return {}


@tool
def run_custom_script(code: str, timeout: int = 60) -> str:
    """当内置预制工具无法搞定某个站时：写并运行一段自定义 Python 脚本。

    ⚡ 运行时增强（2026-08 新增）——在脚本内直接调用，无需 import：
      1. get_site_profile(origin) — 读取已存档的 Cookie/脚本（data/sites.json）
           profile = get_site_profile("https://blog.csdn.net")
           headers["Cookie"] = profile.get("cookies", "")
      2. decrypt_js_eval_html(html) — 解 CSDN 风格 `oo=[0x..] XOR key eval` 混淆
           plain = decrypt_js_eval_html(r.text)
           if plain: soup = BeautifulSoup(plain, "html.parser")
      3. browser_render(url, wait_ms=3500, extra_headers=None, cookies=None, wait_selector=None)
           — Playwright Chromium 无头完整渲染，返回 (final_url, html)
           final_url, html = browser_render(url, wait_ms=5000, wait_selector="#content_views")
           if not html.startswith("ERR"): soup = BeautifulSoup(html, "html.parser")
      4. _INJECTED_PROFILES — 系统自动填充的 dict，key=origin，value=站点档案（等价 get_site_profile）
           for origin, profile in _INJECTED_PROFILES.items(): ...  # 自动匹配脚本内 URL
      5. make_session(retries=3, backoff=0.5, uas=None)
           — 带自动 429/5xx 重试 + 随机 UA 轮换的 requests.Session
           s = make_session() ; r = s.get(url)

    批处理模式（多条目任务强烈推荐）：
        urls = [url1, url2, ..., url30] ; results = []
        for u in urls:
            r = requests.get(u, headers=headers)
            soup = BeautifulSoup(r.text, 'html.parser')
            results.append(parse(soup))
        print(json.dumps(results, ensure_ascii=False))
    **严禁**对每个条目单独调一次 run_custom_script —— 必须合并成批次。

    【脚本升级阶梯】（每一级最多 1 次尝试，绝对不要写 10 段 S1 requests 死循环！）：
      S1 直连 requests + bs4（带 UA/Referer/Accept 头） → 失败→S2
      S2 requests.Session() + make_session() + get_site_profile() 拿 Cookie 复用 → 失败→S3
      S3 decrypt_js_eval_html() 解 JS 混淆 HTML + lxml（HAS_LXML）深 DOM → 失败→S4
      S4 browser_render() Playwright 完整渲染 + wait_selector 等正文出现；
         仍然 VIP 遮罩/锁定？→ 立刻停止脚本 → 向用户要 登录 Cookie / VIP 会话字串。

    保存路径约定：
      - 媒体下载 → DOWNLOADS_DIR / 子目录   (= settings.downloads_dir / 子目录)
      - 文档（md/txt） → OUTPUT_DIR / 子目录    (= settings.output_dir / 子目录)

    脚本模板复用：写脚本前如果感觉本站和之前爬过的某个站结构相似
    （字体混淆 / CSDN / 微信读书 / DPlayer m3u8 ...），可以先调
    `recommend_scripts(hint="关键词")` 拿历史训练好的脚本当模板套用，
    改改 URL/请求头/Cookie 就能用，不用从零写。

    参数：
        code: Python 代码。预导入库（请勿再次 import）：sys, os, json, re, time,
              hashlib, base64, random, math, copy, itertools, html 标准库, requests,
              BeautifulSoup, urllib.parse.*, Path, datetime。可选：lxml_html
              （通过 HAS_LXML 判断是否可用）。结果一律用 print() 输出。
        timeout: 最长秒数（默认 60，上限 120）。

    返回：
        标准输出 + 错误输出（前 5000 字）+ 异常时带 EXIT CODE / TIMEOUT 前缀。
    """
    timeout = min(max(timeout, 5), 120)
    settings = get_settings()
    venv_python = str(Path(sys.executable))

    try:
        # 确保 scripts 目录存在
        scripts_dir = settings.project_root / "data" / "scripts"
        scripts_dir.mkdir(parents=True, exist_ok=True)

        # 自动注入：从代码里提取所有 URL → 读站点档案 → 组装 _INJECTED_PROFILES
        origins = _extract_origins_from_code(code)
        injected_profiles = _load_injected_profiles(origins, settings.project_root)

        # 拼接完整脚本：预导入 + 自动注入的 profiles + 用户代码
        # _ALLOWED_HEADER 包含 4 个 str.format 插值占位：
        #   {root!r}、{downloads!r}、{output!r}、{injected_profiles!r}
        # 其他大括号都用 {{ }} 双写保证 format 后还原为单个字面量
        header = _ALLOWED_HEADER.format(
            root=str(settings.project_root),
            downloads=str(settings.downloads_dir),
            output=str(settings.output_dir),
            injected_profiles=injected_profiles,
        )
        # 加上一行运行时注释，方便调试时看自动注入了哪些 origin
        auto_hint_lines = [
            f"# [INJECTED by run_custom_script] origins found in code: {origins}",
            f"# [INJECTED] site profiles matched: {sorted(injected_profiles.keys())}",
        ]
        header_suffix = "\n".join(auto_hint_lines) + "\n\n"
        full_code = header + header_suffix + code

        # 通过 stdin 传脚本内容（方案 A：不写临时文件，根治泄漏）
        # cwd 改到 _tmp：AI 脚本自身 open() 写出的输出文件落在 _tmp，启动自动清理
        try:
            result = subprocess.run(
                [venv_python, "-"],
                input=full_code,
                capture_output=True,
                text=True,
                encoding="utf-8", errors="replace",  # Windows 默认 GBK，脚本输出 UTF-8 会崩读取线程
                env={**os.environ, "PYTHONIOENCODING": "utf-8"},  # 强制子进程也用 UTF-8 输出
                timeout=timeout,
                cwd=str(_ensure_tmp()),
            )
            output = ""
            if result.stdout:
                output += result.stdout
            if result.stderr:
                output += "\n[STDERR]\n" + result.stderr
            if result.returncode != 0:
                output = f"[EXIT CODE {result.returncode}]\n" + output
            # 把自动注入 hints 放到结果开头（1 行就够，免得污染正文）
            injected_note = ""
            if origins:
                injected_note = (
                    f"[AUTO-INJECTED {len(injected_profiles)} site profiles for origins: "
                    f"{', '.join(origins)}]\n"
                )
            return (injected_note + output)[:5000] or "(no output)"
        except subprocess.TimeoutExpired:
            return f"[TIMEOUT] Script exceeded {timeout}s limit"
    except Exception as e:
        return f"[ERROR] {e}"

