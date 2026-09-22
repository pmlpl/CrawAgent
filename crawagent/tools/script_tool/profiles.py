"""URL 提取 + 站点档案自动注入辅助函数。

从 AI 写的脚本代码中提取所有 URL，按域名匹配 data/sites.json 中的
站点档案（cookies / saved_script / strategy / notes），自动注入到
脚本运行环境中。
"""
import json
import re
from pathlib import Path
from urllib.parse import urlparse

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


