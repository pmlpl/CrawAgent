"""自定义脚本工具 —— 沙箱执行层。

通过子进程执行 AI 写的 Python 脚本，预注入标准库 + 爬虫库 +
能力增强 helper。stdout + stderr 截取前 5000 字符返回。
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

from langchain_core.tools import tool

from crawagent.config.settings import get_settings
from crawagent.tools.session_dir import session_subdir
from .header import _ALLOWED_HEADER
from .profiles import _extract_origins_from_code, _load_injected_profiles

# 模块级路径变量（raw string 外，Python 代码可直接引用）
_s = get_settings()
PROJECT_ROOT = _s.project_root
DOWNLOADS_DIR = _s.downloads_dir
OUTPUT_DIR = _s.output_dir
DATA_TMP_DIR = PROJECT_ROOT / "data" / "_tmp"


def _ensure_tmp() -> Path:
    """确保临时目录存在，并清空目录内所有遗留内容（启动全量清理）。

    方案 A 后本模块不再向 _tmp 写任何临时脚本/JSON；该目录仅作为
    run_custom_script 子进程的 cwd，收容 AI 脚本自己写出的输出文件。
    每次调用无条件清空（文件 + 子目录递归），避免崩溃残留累积；
    修前只清顶层文件，downloads/output/tools 子目录残留膨胀。
    """
    try:
        DATA_TMP_DIR.mkdir(parents=True, exist_ok=True)
        for p in DATA_TMP_DIR.glob("*"):
            try:
                if p.is_dir() and not p.is_symlink():
                    shutil.rmtree(p, ignore_errors=True)
                else:
                    p.unlink(missing_ok=True)
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
      - 文档（md/txt） → OUTPUT_DIR / SESSION_SUBDIR / 文件名（本会话产物目录；SESSION_SUBDIR
        为注入好的子目录名常量，空串表示无会话上下文此时直接用 OUTPUT_DIR）
      - 不要把产物写到桌面/项目根等任意绝对路径

    脚本模板复用：写脚本前如果感觉本站和之前爬过的某个站结构相似
    （字体混淆 / CSDN / 微信读书 / DPlayer m3u8 ...），可以先调
    `recommend_scripts(hint="关键词")` 拿历史训练好的脚本当模板套用，
    改改 URL/请求头/Cookie 就能用，不用从零写。

    参数：
        code: Python 代码。预导入库（请勿再次 import）：sys, os, json, re, time,
              hashlib, base64, random, math, copy, itertools, html 标准库, requests,
              BeautifulSoup, urllib.parse.*, Path, datetime。可选：lxml_html
              （通过 HAS_LXML 判断是否可用）。结果一律用 print() 输出。
        timeout: 最长秒数（默认 60，上限 600）。抓包监听、等设备响应、
              批量下载等慢任务请主动给更大的 timeout，避免 120s 内跑不完被强杀。

    返回：
        标准输出 + 错误输出（前 5000 字）+ 异常时带 EXIT CODE / TIMEOUT 前缀。
    """
    timeout = min(max(timeout, 5), 600)
    from crawagent.tools.script_tool import get_settings as _get_settings
    settings = _get_settings()
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
            session_subdir=session_subdir(),
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

