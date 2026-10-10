"""PC 逆向工具集 — Windows 桌面应用加密接口破解的 frida hook 工具。

与 android_reverse_tool 并列的 PC 侧工具链：Android 版全绑 `-D device_id`（远程设备），
PC 版去掉 `-D`、改 `-n process_name`（attach 本地进程）。一期只做 attach 运行中进程
（-n 模式），spawn + attach（-f 模式）留给二期。

工具清单（4 个）：
  list_windows_processes   — 列本地 Windows 进程（可选按名过滤）
  frida_hook_pc_function   — frida-trace hook 本地进程函数（native 符号 glob）
  frida_dump_dll           — dump 本地进程 DLL 模块基址 + 导出偏移
  frida_bypass_pc_ssl      — 注入 Windows SSL bypass 脚本
"""
from __future__ import annotations

import csv
import io
import subprocess
import sys
from pathlib import Path

from langchain_core.tools import tool

# 复用 android_reverse_tool 的辅助函数（015 三级查找 + frida CLI 包装 + 截断）
from crawagent.tools.android_reverse_tool import (
    _check_binary,
    _run_frida,
    _truncate,
    _rel_path,
    _DUMP_SO_JS_TEMPLATE,
)

# PC 侧 SSL bypass 脚本随包资源路径
_ASSETS_DIR = Path(__file__).resolve().parent / "assets"


def _pc_ssl_bypass_js_path() -> Path:
    """返回内置 PC SSL bypass JS 脚本路径。"""
    return _ASSETS_DIR / "pc_ssl_bypass.js"


def _list_processes_via_tasklist(pattern: str, timeout: int) -> tuple[int, str]:
    """调 tasklist /Fo CSV /Nh 列进程，返回 (rc, stdout)。

    tasklist 仅 Windows 自带；非 Windows 平台返回 (-1, ERR 提示串)。
    """
    if sys.platform != "win32":
        return -1, "ERR: tasklist 仅 Windows 自带，当前平台不支持 list_windows_processes"
    try:
        r = subprocess.run(
            ["tasklist", "/Fo", "CSV", "/Nh"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        out = (r.stdout or "") + (r.stderr or "")
        return r.returncode, out
    except subprocess.TimeoutExpired:
        return -1, f"ERR: tasklist 超时（{timeout}s）"
    except FileNotFoundError:
        return -1, "ERR: tasklist 未安装（仅 Windows 自带；非 Windows 二期补 POSIX ps）"
    except Exception as e:
        return -1, f"ERR: tasklist 执行失败: {e}"


# ---------------------------------------------------------------------------
# 工具 1: list_windows_processes
# ---------------------------------------------------------------------------

@tool
def list_windows_processes(pattern: str = "", timeout: int = 10) -> str:
    """列出本地 Windows 进程（可选按名过滤）。

    返回每条进程的 PID + 进程名 + 架构（32bit/64bit）。pattern 空则列全部，
    非空则按不区分大小写的子串过滤。结果排除 frida 自身进程（避免 agent hook 自己）。

    在任何 PC frida 工具调用前先调它确认目标进程在运行。
    无 tasklist / 无匹配进程 / 全空都返回特定状态串，照原样上报。
    """
    rc, out = _list_processes_via_tasklist(pattern, timeout)
    if rc != 0:
        return out  # ERR 串直接回传
    # 解析 CSV：每行 "name","pid","session","sessionnum","mem"
    procs: list[dict] = []
    reader = csv.reader(io.StringIO(out))
    for row in reader:
        if not row or len(row) < 2:
            continue
        name = row[0].strip().strip('"')
        pid = row[1].strip().strip('"')
        if not name or not pid.isdigit():
            continue
        # 排除 frida 自身进程（frida.exe / frida-trace.exe）避免 hook 自己
        lname = name.lower()
        if lname in ("frida.exe", "frida-trace.exe"):
            continue
        # 架构判断：tasklist 不直接给 arch，64bit 进程名通常无 32 标记，
        # 32bit 进程在 64bit 系统会显示带 *32 后缀（老系统）；新版用 Image Name 列不含 arch。
        # 这里用 pid 范围粗略判断不可靠，干脆不标 arch，统一显示 "(arch via tasklist 不可得)"。
        procs.append({"pid": int(pid), "name": name})
    # pattern 过滤
    if pattern:
        pat = pattern.lower()
        procs = [p for p in procs if pat in p["name"].lower()]
    if not procs:
        return f"NO_PROCESS: 未找到匹配进程（pattern={pattern!r}）"
    lines = [f"pid={p['pid']} | name={p['name']}" for p in procs]
    return f"找到 {len(procs)} 个进程:\n" + "\n".join(lines)


# ---------------------------------------------------------------------------
# 工具 2: frida_hook_pc_function
# ---------------------------------------------------------------------------

@tool
def frida_hook_pc_function(process_name: str, function_pattern: str,
                           max_calls: int = 20, timeout: int = 30) -> str:
    """用 frida-trace hook 本地 Windows 进程的函数（native 符号 glob）。

    参数：
        process_name:   目标进程名（如 QQMusic.exe）；按 -n attach 运行中进程。
        function_pattern: native 符号 glob（*CryptEncrypt* / *sign* / *AES*）。
        max_calls:      最多记录调用次数（防刷屏；通过 timeout 兜底截断）。
        timeout:        frida-trace 运行秒数（持续运行到 timeout 后子进程被 kill）。

    与 Android 版差异：去掉 -D device_id、去掉 -f package_name（一期不做 spawn），
    去掉 Java 检测逻辑（PC 进程无 Java VM，统一走 -i native glob）。
    """
    if not _check_binary("frida-trace"):
        return "ERR: frida-trace 未安装（pip install frida-tools 后重试）"
    cmd = [
        "frida-trace",
        "-n", process_name,
        "--no-pause",
        "-i", function_pattern,
    ]
    rc, out = _run_frida(cmd, timeout=timeout)
    header = (
        f"[frida-trace] process={process_name} pattern={function_pattern} "
        f"calls_cap={max_calls}\n"
    )
    return header + _truncate(out)


# ---------------------------------------------------------------------------
# 工具 3: frida_dump_dll
# ---------------------------------------------------------------------------

@tool
def frida_dump_dll(process_name: str, dll_name: str,
                   dump_offsets: bool = True, timeout: int = 30) -> str:
    """附加本地进程，dump 指定 DLL 的基址 + 导出函数偏移。

    参数：
        process_name: 目标进程名（如 QQMusic.exe）。
        dll_name:     DLL 基名（ncrypt.dll / libcrypto.dll）。
        dump_offsets: True 时枚举常见导出符号偏移（Crypt* / SSL* / sign* / encrypt*）。
        timeout:      frida 运行秒数。

    复用 _DUMP_SO_JS_TEMPLATE（Process.findModuleByName 对 DLL 同样有效）。
    """
    if not _check_binary("frida"):
        return "ERR: frida 未安装（pip install frida-tools 后重试）"
    if dump_offsets:
        exports_block = (
            "var exps = m.enumerateExports();"
            "exps.forEach(function (e) {"
            "  var off = e.address.sub(m.base);"
            "  console.log('EXPORT: ' + e.name + ' @ ' + e.address + ' (offset=' + off + ')');"
            "});"
        )
    else:
        exports_block = "console.log('(dump_offsets=false，跳过导出枚举)');"
    js = (
        _DUMP_SO_JS_TEMPLATE
        .replace("__SO_NAME__", dll_name.replace("\\", "\\\\").replace("'", "\\'"))
        .replace("__EXPORTS_BLOCK__", exports_block)
    )
    cmd = ["frida", "-n", process_name, "--no-pause", "-e", js]
    rc, out = _run_frida(cmd, timeout=timeout)
    header = f"[dump_dll] process={process_name} dll={dll_name}\n"
    if "BASE:" not in out and ("ERR" in out or "Error" in out):
        return header + f"[DUMP_FAIL] 无法 dump {dll_name}（DLL 未加载或进程未运行）\n{_truncate(out)}"
    return header + _truncate(out)


# ---------------------------------------------------------------------------
# 工具 4: frida_bypass_pc_ssl
# ---------------------------------------------------------------------------

@tool
def frida_bypass_pc_ssl(process_name: str, timeout: int = 30) -> str:
    """注入 Windows SSL bypass 脚本，绕过本地进程的证书校验。

    覆盖 Windows 常见 SSL 栈：OpenSSL/BoringSSL（SSL_get_verify_result）、
    SChannel（SecPkgConnectionVerify）、Node.js TLS（tls.checkServerIdentity）、
    CryptoAPI（CertVerifyCertificateChainPolicy）。

    挂上后立刻走 MCP Capture Workflow 抓明文流量（bypass 与 capture 串联）。
    """
    if not _check_binary("frida"):
        return "ERR: frida 未安装（pip install frida-tools 后重试）"
    js_path = _pc_ssl_bypass_js_path()
    if not js_path.is_file():
        return f"[BYPASS_FAIL] 内置 PC SSL bypass 脚本缺失: {_rel_path(js_path)}"
    cmd = [
        "frida",
        "-n", process_name,
        "--no-pause",
        "-l", str(js_path),
    ]
    rc, out = _run_frida(cmd, timeout=timeout)
    bypass_count = out.count("[BYPASS]")
    err_markers = ("Error:", "failed", "Exception", "BYPASS_FAIL")
    has_error = any(m.lower() in out.lower() for m in err_markers) and bypass_count == 0
    header = f"[pc_ssl_bypass] process={process_name} script={_rel_path(js_path)}\n"
    if has_error and bypass_count == 0:
        return header + "[BYPASS_FAIL] 绕过失败，检查进程是否在运行 / 是否用了未覆盖的 SSL 栈\n" + _truncate(out)
    return header + f"[BYPASS_OK] 已注入 PC SSL bypass 脚本（{bypass_count} 处绕过点命中）\n" + _truncate(out)
