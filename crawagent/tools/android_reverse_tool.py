"""Android 逆向工具集 — 给 AI Agent 用的 frida hook / adb 操作工具。

所有工具都用 subprocess 调命令行（adb / frida / frida-trace），不绑 frida Python API。
缺二进制时返回友好错误串，不抛异常；import 时不调 subprocess。

工具清单（6 个）：
  list_adb_devices          — 列出 adb 连接的设备
  install_apk              — adb install 装 APK
  push_file                — adb push 推文件到设备
  frida_hook_function      — frida-trace hook 函数（打印调用栈/参数/返回值）
  frida_dump_so            — frida dump native so 基址 + 导出偏移
  frida_bypass_ssl_pinning — frida 绕过 SSL Pinning
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from langchain_core.tools import tool

from crawagent.config.settings import get_settings

# 内置 assets 目录（SSL pinning bypass 脚本等随包资源）
_ASSETS_DIR = Path(__file__).resolve().parent / "assets"

# Windows 常见 adb 安装位置（Android SDK / 主流模拟器；015）。
# frida 靠 pip 装进 venv Scripts，which 天然覆盖，不设位置表。
_ADB_COMMON_LOCATIONS = (
    "%LOCALAPPDATA%/Android/Sdk/platform-tools/adb.exe",
    "C:/Android/Sdk/platform-tools/adb.exe",
    "%ProgramFiles%/Netease/MuMuPlayer-12.0/shell/adb.exe",
    "%ProgramFiles(x86)%/Netease/MuMuPlayer-12.0/shell/adb.exe",
    "C:/LDPlayer/LDPlayer9/adb.exe",
    "%ProgramFiles%/LDPlayer/LDPlayer9/adb.exe",
    "%ProgramFiles%/Nox/bin/adb.exe",
)

# 二进制路径命中缓存（进程内扫盘只一次）；miss 故意不缓存，装完软件即时生效
_BINARY_PATH_CACHE: dict[str, str] = {}

# 工具名 → settings 显式配置字段（frida-trace 与 frida 同目录，共用 frida_path）
_BINARY_SETTING_KEYS = {"adb": "adb_path", "frida": "frida_path", "frida-trace": "frida_path"}

# frida_dump_so 用的内联 JS 模板（占位符 __SO_NAME__ / __EXPORTS_BLOCK__ 运行时替换）
_DUMP_SO_JS_TEMPLATE = """\
var soName = '__SO_NAME__';
function _dump() {
  var m = Process.findModuleByName(soName);
  if (!m) return false;
  console.log('BASE: ' + m.base);
  console.log('SIZE: ' + m.size);
  __EXPORTS_BLOCK__
  return true;
}
if (!_dump()) {
  // so 可能延迟加载：每 500ms 重试一次，最多 20 次（10 秒）后放弃
  var _tries = 0;
  var _timer = setInterval(function () {
    _tries = _tries + 1;
    if (_dump() || _tries === 20) {
      clearInterval(_timer);
    }
  }, 500);
}
"""


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------

def _settings_binary_override(name: str) -> str:
    """读 .env 显式配置的路径（adb_path/frida_path）；配置存在且是文件才认。"""
    key = _BINARY_SETTING_KEYS.get(name)
    if not key:
        return ""
    val = getattr(get_settings(), key, "") or ""
    if val and Path(val).is_file():
        return val
    return ""


def _common_location_binary(name: str) -> str:
    """扫 Windows 常见安装位置（仅 adb 有表）；命中返回路径。"""
    if name != "adb":
        return ""
    for tpl in _ADB_COMMON_LOCATIONS:
        p = Path(os.path.expandvars(tpl))
        if p.is_file():
            return str(p)
    return ""


def _check_binary(name: str) -> str | None:
    """三级查找二进制（015）：PATH → settings 显式配置 → Windows 常见位置。

    命中缓存进程内复用（扫盘只一次）；miss 不缓存——用户装完 adb 下次调用即生效。
    可用返回路径，缺返回 None。
    """
    if name in _BINARY_PATH_CACHE:
        return _BINARY_PATH_CACHE[name]
    found = shutil.which(name) or shutil.which(name + ".exe")
    if not found:
        found = _settings_binary_override(name)
    if not found:
        found = _common_location_binary(name)
    if found:
        _BINARY_PATH_CACHE[name] = found
        return found
    return None


def _run_adb(args: list[str], device_id: str = "", timeout: int = 30) -> tuple[int, str]:
    """subprocess.run 调 adb。device_id 非空时加 -s 指定设备。

    返回 (returncode, stdout+stderr 合并)。缺 adb 二进制时返回 (-1, ERR 提示串)。
    """
    resolved = _check_binary("adb")
    if not resolved:
        return -1, "ERR: adb 未安装（装 platform-tools，或在 .env 设 ADB_PATH 后重试）"
    # 015 三级查找解析出的绝对路径要真正用于执行——PATH 无 adb 时裸 "adb" 会 WinError 2
    cmd = [resolved]
    if device_id:
        cmd += ["-s", device_id]
    cmd += args
    try:
        r = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        out = (r.stdout or "") + (r.stderr or "")
        return r.returncode, out
    except subprocess.TimeoutExpired:
        return -1, f"ERR: adb 命令超时（{timeout}s）"
    except Exception as e:
        return -1, f"ERR: adb 执行失败: {e}"


def _run_frida(args: list[str], timeout: int = 30) -> tuple[int, str]:
    """subprocess.run 调 frida / frida-trace。

    args 是完整命令列表（首元素为 frida 或 frida-trace）。
    返回 (returncode, stdout+stderr 合并)。缺二进制时返回 (-1, ERR 提示串)。
    """
    if not args:
        return -1, "ERR: 空命令"
    binary = args[0]
    resolved = _check_binary(binary)
    if not resolved:
        hint = "pip install frida-tools" if binary.startswith("frida") else f"安装 {binary}"
        return -1, f"ERR: {binary} 未安装（{hint} 后重试）"
    try:
        r = subprocess.run(
            [resolved, *args[1:]],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        out = (r.stdout or "") + (r.stderr or "")
        return r.returncode, out
    except subprocess.TimeoutExpired:
        return -1, f"ERR: {binary} 命令超时（{timeout}s）"
    except Exception as e:
        return -1, f"ERR: {binary} 执行失败: {e}"


def _ssl_pinning_js_path() -> Path:
    """返回内置 SSL pinning bypass JS 脚本路径。"""
    return _ASSETS_DIR / "ssl_pinning_bypass.js"


def _truncate(text: str, limit: int = 4000) -> str:
    """截断长文本，保留前 limit 字符并附截断标记。"""
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... (截断，共 {len(text)} 字符)"


def _rel_path(p: Path) -> str:
    """把绝对路径相对 project_root 显示（更可读）；逃逸时返回原绝对路径。"""
    try:
        return str(p.relative_to(get_settings().project_root))
    except Exception:
        return str(p)


# ---------------------------------------------------------------------------
# 工具 1: list_adb_devices
# ---------------------------------------------------------------------------

@tool
def list_adb_devices() -> str:
    """列出当前通过 adb 连接的 Android 设备（含已 root 的真机/模拟器）。

    返回每台设备的 serial、状态（device/offline/unauthorized）、型号与 Android 版本。
    在任何 frida hook / apk 安装 / 推文件操作前先调它确认设备在线且已授权 USB 调试。
    无设备时返回 "NO_DEVICE: 请先 adb connect / 插 USB 并授权调试"。
    缺 adb 二进制时返回 "ERR: adb 未安装（装 platform-tools，或在 .env 设 ADB_PATH 后重试）"。
    """
    if not _check_binary("adb"):
        return "ERR: adb 未安装（装 platform-tools，或在 .env 设 ADB_PATH 后重试）"
    rc, out = _run_adb(["devices", "-l"], timeout=10)
    if rc != 0:
        return f"ERR: adb devices 失败: {out.strip()}"
    # 解析 "List of devices attached" 之后的行
    valid_statuses = {"device", "offline", "unauthorized", "recovery", "sideload"}
    device_lines: list[list[str]] = []
    for line in out.splitlines():
        line = line.strip()
        if not line or line.startswith("List of devices"):
            continue
        parts = line.split()
        if len(parts) < 2 or parts[1] not in valid_statuses:
            # 跳过 "* daemon started successfully" 等 adb 自身的提示行
            continue
        device_lines.append(parts)
    if not device_lines:
        return "NO_DEVICE: 请先 adb connect / 插 USB 并授权调试"
    results: list[str] = []
    for parts in device_lines:
        serial = parts[0]
        status = parts[1]  # device / offline / unauthorized
        model = ""
        for token in parts[2:]:
            if token.startswith("model:"):
                model = token.split(":", 1)[1]
        android_ver = ""
        if status == "device":
            rc2, ver_out = _run_adb(
                ["shell", "getprop", "ro.build.version.release"],
                device_id=serial, timeout=8,
            )
            if rc2 == 0:
                android_ver = ver_out.strip()
        results.append(
            f"serial={serial} | status={status} | "
            f"model={model or '(未知)'} | android={android_ver or '(未知)'}"
        )
    return f"找到 {len(results)} 台设备:\n" + "\n".join(results)


# ---------------------------------------------------------------------------
# 工具 2: install_apk
# ---------------------------------------------------------------------------

@tool
def install_apk(device_id: str, apk_path: str, reinstall: bool = False, timeout: int = 120) -> str:
    """通过 adb 给指定设备安装 APK。

    参数：
        device_id:  list_adb_devices 返回的 serial。
        apk_path:   本地 APK 绝对路径。
        reinstall:  True = 保留数据覆盖安装（adb install -r）。
        timeout:    adb install 超时秒（大 APK 默认 120）。

    返回 adb install 的 stdout/stderr 摘要 + 成功/失败标记。
    """
    if not _check_binary("adb"):
        return "ERR: adb 未安装（装 platform-tools，或在 .env 设 ADB_PATH 后重试）"
    apk = Path(apk_path)
    if not apk.is_file():
        return f"ERR: APK 不存在: {apk_path}"
    cmd = ["install"]
    if reinstall:
        cmd.append("-r")
    cmd.append(str(apk.resolve()))
    rc, out = _run_adb(cmd, device_id=device_id, timeout=timeout)
    if rc == 0 and "Success" in out:
        return f"OK: APK 安装成功 ({apk.name})\n{out.strip()}"
    return f"FAIL: APK 安装失败 (rc={rc})\n{_truncate(out)}"


# ---------------------------------------------------------------------------
# 工具 3: push_file
# ---------------------------------------------------------------------------

@tool
def push_file(device_id: str, local_path: str, remote_path: str, timeout: int = 60) -> str:
    """把本地文件推到设备指定路径（adb push）。

    典型用途：推 frida-server 到 /data/local/tmp/ 后 chmod + nohup 启动；
    推自定义 frida JS 脚本到设备后续 frida -l 引用。
    remote_path 必须是设备绝对路径，无写权限时返回 ERR。
    """
    if not _check_binary("adb"):
        return "ERR: adb 未安装（装 platform-tools，或在 .env 设 ADB_PATH 后重试）"
    local = Path(local_path)
    if not local.is_file():
        return f"ERR: 本地文件不存在: {local_path}"
    rc, out = _run_adb(
        ["push", str(local.resolve()), remote_path],
        device_id=device_id, timeout=timeout,
    )
    if rc == 0:
        return f"OK: 已推送 {local.name} -> {remote_path}\n{out.strip()}"
    return f"FAIL: 推送失败 (rc={rc})\n{_truncate(out)}"


# ---------------------------------------------------------------------------
# 工具 4: frida_hook_function
# ---------------------------------------------------------------------------

@tool
def frida_hook_function(device_id: str, package_name: str, function_pattern: str,
                        max_calls: int = 20, timeout: int = 30) -> str:
    """用 frida-trace hook App 中匹配名称的函数，打印调用栈/参数/返回值。

    适用场景：定位加密签名函数（sign/token/signature）、追踪关键 API 调用链。
    function_pattern 支持 Java 全限定名（com.xx.Util.sign）或 native 符号 glob（*crypt*）。
    最多记录 max_calls 次调用后退出（防刷屏）。

    需要：设备已 root、frida-server 在设备上运行、本机装 frida-tools。
    缺依赖时返回 ERR + 安装提示。
    """
    if not _check_binary("frida-trace"):
        return "ERR: frida-trace 未安装（pip install frida-tools 后重试）"
    # 判断 Java 还是 native：含点号且非 * 开头视为 Java 全限定名，否则 native 符号 glob
    is_java = "." in function_pattern and not function_pattern.startswith("*")
    if is_java and "!" not in function_pattern:
        # com.xx.Util.sign -> com.xx.Util!sign（frida-trace 用 ! 分隔类与方法）
        pieces = function_pattern.rsplit(".", 1)
        pattern = pieces[0] + "!" + pieces[1] if len(pieces) == 2 else function_pattern
    else:
        pattern = function_pattern
    flag = "-j" if is_java else "-i"
    cmd = [
        "frida-trace",
        "-D", device_id,
        "-f", package_name,
        "--no-pause",
        flag, pattern,
    ]
    # frida-trace 持续运行；timeout 到后子进程被 kill，已采集的输出返回
    rc, out = _run_frida(cmd, timeout=timeout)
    header = (
        f"[frida-trace] device={device_id} pkg={package_name} "
        f"pattern={function_pattern} java={is_java} calls_cap={max_calls}\n"
    )
    return header + _truncate(out)


# ---------------------------------------------------------------------------
# 工具 5: frida_dump_so
# ---------------------------------------------------------------------------

@tool
def frida_dump_so(device_id: str, package_name: str, so_name: str,
                  dump_offsets: bool = True, timeout: int = 30) -> str:
    """附加目标进程，dump 指定 native so 的基址 + 导出函数偏移。

    用途：逆向 so 加密（拿到基址后可用 frida read/write 内存、IDA 配合定位算法）。
    so_name 匹配 Module.findExportByName 探测的模块名（如 libnative.so、libencrypt.so）。
    dump_offsets=True 时顺带列出常见导出符号（Java_*、SSL_*、AES_*）的偏移。

    返回 "BASE: 0x... | EXPORTS: name=0xoffset ..."。
    """
    if not _check_binary("frida"):
        return "ERR: frida 未安装（pip install frida-tools 后重试）"
    # 构造内联 JS：查找模块基址 + 可选枚举导出偏移
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
        .replace("__SO_NAME__", so_name.replace("\\", "\\\\").replace("'", "\\'"))
        .replace("__EXPORTS_BLOCK__", exports_block)
    )
    cmd = ["frida", "-D", device_id, "-f", package_name, "--no-pause", "-e", js]
    rc, out = _run_frida(cmd, timeout=timeout)
    header = f"[dump_so] device={device_id} pkg={package_name} so={so_name}\n"
    if "BASE:" not in out and ("ERR" in out or "Error" in out):
        return header + f"[DUMP_FAIL] 无法 dump {so_name}（so 未加载或 frida-server 未运行）\n{_truncate(out)}"
    return header + _truncate(out)


# ---------------------------------------------------------------------------
# 工具 6: frida_bypass_ssl_pinning
# ---------------------------------------------------------------------------

@tool
def frida_bypass_ssl_pinning(device_id: str, package_name: str, timeout: int = 30) -> str:
    """用内置 frida 脚本绕过 App 的 SSL Pinning（证书绑定校验）。

    绕过后即可用普通抓包代理（anything-analyzer MCP）明文看 HTTPS 请求 ——
    完成本步后请走 system.md 的 MCP Capture Workflow 抓真实 API。

    覆盖常见 Pinning 实现：OkHttp3 CertificatePinning、TrustManager 自定义校验、
    Conscrypt、Flutter 的 ssl_verify。失败时返回 [PINNING_FAIL] + 原因，建议改用
    frida_hook_function 自行 patch 校验函数。
    """
    if not _check_binary("frida"):
        return "ERR: frida 未安装（pip install frida-tools 后重试）"
    js_path = _ssl_pinning_js_path()
    if not js_path.is_file():
        return f"[PINNING_FAIL] 内置 SSL pinning bypass 脚本缺失: {_rel_path(js_path)}"
    cmd = [
        "frida",
        "-D", device_id,
        "-f", package_name,
        "--no-pause",
        "-l", str(js_path),
    ]
    rc, out = _run_frida(cmd, timeout=timeout)
    # frida 持续运行直到 timeout；检查输出里有无 BYPASS 标记或异常
    bypass_count = out.count("[BYPASS]")
    err_markers = ("Error:", "failed", "Exception", "PINNING_FAIL")
    has_error = any(m.lower() in out.lower() for m in err_markers) and bypass_count == 0
    header = f"[ssl_pinning] device={device_id} pkg={package_name} script={_rel_path(js_path)}\n"
    if has_error and bypass_count == 0:
        return header + "[PINNING_FAIL] 绕过失败，检查 frida-server 是否运行 / App 是否用自定义校验\n" + _truncate(out)
    return header + f"[PINNING_OK] 已注入 bypass 脚本（{bypass_count} 处绕过点命中）\n" + _truncate(out)
