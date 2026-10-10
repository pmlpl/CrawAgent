"""PC 逆向工具测试 — 全离线，mock subprocess / _check_binary / _run_frida，不真连进程。

覆盖（规格第五节）：
  1. 进程列表解析（tasklist CSV → PID/name；pattern 过滤；空结果 NO_PROCESS）
  2. frida 命令构造（-n process_name --no-pause -i pattern，不含 -D）
  3. dump DLL JS 模板（含 findModuleByName + dump_offsets 跳过标记）
  4. SSL bypass 脚本存在性 + 段标记
  5. 错误提示串（frida 未安装 / 进程不存在）
  6. 输出截断
  7. system.md 一致性（工具条目数 = 49；PC Workflow 段含 Step 0-3）
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from crawagent.tools import pc_reverse_tool as pct
from crawagent.tools.pc_reverse_tool import (
    list_windows_processes,
    frida_hook_pc_function,
    frida_dump_dll,
    frida_bypass_pc_ssl,
    _check_binary,
    _truncate,
    _pc_ssl_bypass_js_path,
)


# ---- list_windows_processes: tasklist CSV 解析 ----

def test_list_processes_parses_csv(monkeypatch):
    """tasklist CSV 输出应解析成 pid + name 条目。"""
    fake_csv = (
        '"QQMusic.exe","1234","Console","1","50,000 K"\r\n'
        '"chrome.exe","5678","Console","1","200,000 K"\r\n'
    )

    class _R:
        returncode = 0
        stdout = fake_csv
        stderr = ""

    monkeypatch.setattr(pct.sys, "platform", "win32")
    monkeypatch.setattr(pct.subprocess, "run", lambda *a, **k: _R())
    r = list_windows_processes.func()
    assert "找到 2 个进程" in r
    assert "pid=1234" in r and "QQMusic.exe" in r
    assert "pid=5678" in r and "chrome.exe" in r


def test_list_processes_pattern_filter(monkeypatch):
    """pattern 应做不区分大小写的子串过滤。"""
    fake_csv = (
        '"QQMusic.exe","1234","Console","1","50 K"\r\n'
        '"chrome.exe","5678","Console","1","200 K"\r\n'
    )

    class _R:
        returncode = 0
        stdout = fake_csv
        stderr = ""

    monkeypatch.setattr(pct.sys, "platform", "win32")
    monkeypatch.setattr(pct.subprocess, "run", lambda *a, **k: _R())
    r = list_windows_processes.func(pattern="qqmusic")
    assert "找到 1 个进程" in r
    assert "QQMusic.exe" in r
    assert "chrome.exe" not in r


def test_list_processes_no_match(monkeypatch):
    """无匹配进程返回 NO_PROCESS。"""
    fake_csv = '"explorer.exe","9999","Console","1","10 K"\r\n'

    class _R:
        returncode = 0
        stdout = fake_csv
        stderr = ""

    monkeypatch.setattr(pct.sys, "platform", "win32")
    monkeypatch.setattr(pct.subprocess, "run", lambda *a, **k: _R())
    r = list_windows_processes.func(pattern="QQMusic")
    assert "NO_PROCESS" in r


def test_list_processes_excludes_frida_self(monkeypatch):
    """结果应排除 frida 自身进程（避免 agent hook 自己）。"""
    fake_csv = (
        '"frida.exe","111","Console","1","10 K"\r\n'
        '"QQMusic.exe","222","Console","1","10 K"\r\n'
        '"frida-trace.exe","333","Console","1","10 K"\r\n'
    )

    class _R:
        returncode = 0
        stdout = fake_csv
        stderr = ""

    monkeypatch.setattr(pct.sys, "platform", "win32")
    monkeypatch.setattr(pct.subprocess, "run", lambda *a, **k: _R())
    r = list_windows_processes.func()
    assert "找到 1 个进程" in r
    assert "frida.exe" not in r and "frida-trace.exe" not in r
    assert "QQMusic.exe" in r


def test_list_processes_non_windows(monkeypatch):
    """非 Windows 平台返回 ERR（tasklist 仅 Windows 自带）。"""
    monkeypatch.setattr(pct.sys, "platform", "linux")
    r = list_windows_processes.func()
    assert "ERR" in r and "tasklist" in r


def test_list_processes_tasklist_missing(monkeypatch):
    """tasklist 未安装（FileNotFoundError）返回 ERR。"""
    monkeypatch.setattr(pct.sys, "platform", "win32")

    def raise_fnf(*a, **k):
        raise FileNotFoundError("tasklist not found")

    monkeypatch.setattr(pct.subprocess, "run", raise_fnf)
    r = list_windows_processes.func()
    assert "ERR" in r and "tasklist" in r


# ---- frida_hook_pc_function: 命令构造（-n 无 -D）----

def test_hook_pc_no_frida_trace(monkeypatch):
    """frida-trace 未安装返回 ERR + 安装提示。"""
    monkeypatch.setattr(pct, "_check_binary", lambda name: None)
    r = frida_hook_pc_function.func("QQMusic.exe", "*sign*")
    assert "ERR" in r and "frida-trace 未安装" in r


def test_hook_pc_command_no_D(monkeypatch):
    """PC 命令应含 -n process_name --no-pause -i pattern，不含 -D。"""
    monkeypatch.setattr(pct, "_check_binary", lambda name: "/frida-trace")
    captured = {}

    def fake_run(args, timeout=30):
        captured["args"] = args
        return (0, "instrumented")

    monkeypatch.setattr(pct, "_run_frida", fake_run)
    r = frida_hook_pc_function.func("QQMusic.exe", "*CryptEncrypt*")
    assert "-n" in captured["args"]
    assert "QQMusic.exe" in captured["args"]
    assert "--no-pause" in captured["args"]
    assert "-i" in captured["args"]
    assert "*CryptEncrypt*" in captured["args"]
    # 关键：PC 版不含 -D（远程设备参数是 Android 专用）
    assert "-D" not in captured["args"]
    assert "-f" not in captured["args"]  # 一期不做 spawn
    # header 应标记 process 而非 device/pkg
    assert "process=QQMusic.exe" in r


def test_hook_pc_no_java_detection(monkeypatch):
    """PC 版不走 Java 检测（无 -j flag，统一 -i native glob）。"""
    monkeypatch.setattr(pct, "_check_binary", lambda name: "/frida-trace")
    captured = {}

    def fake_run(args, timeout=30):
        captured["args"] = args
        return (0, "ok")

    monkeypatch.setattr(pct, "_run_frida", fake_run)
    # 即使 pattern 含点号（Windows 函数名如 kernel32.CryptEncrypt）也不走 -j
    frida_hook_pc_function.func("proc", "kernel32.CryptEncrypt")
    assert "-j" not in captured["args"]
    assert "-i" in captured["args"]


def test_hook_pc_truncates_long_output(monkeypatch):
    """输出超长应被截断。"""
    monkeypatch.setattr(pct, "_check_binary", lambda name: "/frida-trace")
    monkeypatch.setattr(pct, "_run_frida",
                        lambda args, timeout=30: (0, "x" * 10000))
    r = frida_hook_pc_function.func("proc", "*sign*")
    assert len(r) < 10000
    assert "截断" in r


# ---- frida_dump_dll: JS 模板替换 ----

def test_dump_dll_no_frida(monkeypatch):
    monkeypatch.setattr(pct, "_check_binary", lambda name: None)
    r = frida_dump_dll.func("proc", "ncrypt.dll")
    assert "ERR" in r and "frida 未安装" in r


def test_dump_dll_js_template_substitution(monkeypatch):
    """JS 模板 __SO_NAME__ 应被替换成 dll_name。"""
    monkeypatch.setattr(pct, "_check_binary", lambda name: "/frida")
    captured = {}

    def fake_run(args, timeout=30):
        captured["js"] = args[-1]  # -e 后的 JS
        return (0, "BASE: 0x1234")

    monkeypatch.setattr(pct, "_run_frida", fake_run)
    frida_dump_dll.func("proc", "ncrypt.dll")
    assert "ncrypt.dll" in captured["js"]
    assert "__SO_NAME__" not in captured["js"]
    # Process.findModuleByName 对 DLL 同样有效（复用模板）
    assert "Process.findModuleByName" in captured["js"]


def test_dump_dll_command_no_D(monkeypatch):
    """dump DLL 命令应含 -n process_name，不含 -D/-f。"""
    monkeypatch.setattr(pct, "_check_binary", lambda name: "/frida")
    captured = {}

    def fake_run(args, timeout=30):
        captured["args"] = args
        return (0, "BASE: 0x1234")

    monkeypatch.setattr(pct, "_run_frida", fake_run)
    frida_dump_dll.func("QQMusic.exe", "libcrypto.dll")
    assert "-n" in captured["args"]
    assert "QQMusic.exe" in captured["args"]
    assert "-D" not in captured["args"]
    assert "-f" not in captured["args"]


def test_dump_dll_offsets_flag(monkeypatch):
    """dump_offsets=False 时 JS 含跳过标记。"""
    monkeypatch.setattr(pct, "_check_binary", lambda name: "/frida")
    captured = {}

    def fake_run(args, timeout=30):
        captured["js"] = args[-1]
        return (0, "BASE: 0x1")

    monkeypatch.setattr(pct, "_run_frida", fake_run)
    frida_dump_dll.func("proc", "ncrypt.dll", dump_offsets=False)
    assert "跳过导出枚举" in captured["js"]
    assert "enumerateExports" not in captured["js"]


def test_dump_dll_fail(monkeypatch):
    """dump 失败（输出含 Error 且无 BASE）应返回 DUMP_FAIL。"""
    monkeypatch.setattr(pct, "_check_binary", lambda name: "/frida")
    monkeypatch.setattr(pct, "_run_frida",
                        lambda args, timeout=30: (1, "Error: module not loaded"))
    r = frida_dump_dll.func("proc", "ncrypt.dll")
    assert "DUMP_FAIL" in r


# ---- frida_bypass_pc_ssl: 脚本存在性 + 段标记 ----

def test_bypass_pc_ssl_no_frida(monkeypatch):
    monkeypatch.setattr(pct, "_check_binary", lambda name: None)
    r = frida_bypass_pc_ssl.func("proc")
    assert "ERR" in r and "frida 未安装" in r


def test_bypass_pc_ssl_script_exists():
    """内置 PC SSL bypass 脚本文件存在。"""
    p = _pc_ssl_bypass_js_path()
    assert p.is_file(), f"PC SSL bypass 脚本缺失: {p}"


def test_bypass_pc_ssl_script_segments():
    """脚本内容含 4 段 SSL 栈的关键符号标记。"""
    content = _pc_ssl_bypass_js_path().read_text(encoding="utf-8")
    # 段 1: OpenSSL / BoringSSL
    assert "SSL_get_verify_result" in content
    # 段 2: SChannel
    assert "SecPkgConnectionVerify" in content or "SslVerifyCertificate" in content
    # 段 3: Node.js TLS
    assert "tls.checkServerIdentity" in content or "checkServerIdentity" in content
    # 段 4: CryptoAPI
    assert "CertVerifyCertificateChainPolicy" in content


def test_bypass_pc_ssl_command_no_D(monkeypatch):
    """bypass 命令应含 -n process_name -l script.js，不含 -D。"""
    monkeypatch.setattr(pct, "_check_binary", lambda name: "/frida")
    captured = {}

    def fake_run(args, timeout=30):
        captured["args"] = args
        return (0, "[BYPASS] OpenSSL")

    monkeypatch.setattr(pct, "_run_frida", fake_run)
    frida_bypass_pc_ssl.func("QQMusic.exe")
    assert "-n" in captured["args"]
    assert "QQMusic.exe" in captured["args"]
    assert "-l" in captured["args"]
    assert "-D" not in captured["args"]
    assert "-f" not in captured["args"]


def test_bypass_pc_ssl_success(monkeypatch):
    """输出含 [BYPASS] 标记应返回 BYPASS_OK + 命中数。"""
    monkeypatch.setattr(pct, "_check_binary", lambda name: "/frida")
    monkeypatch.setattr(pct, "_run_frida",
                        lambda args, timeout=30: (0, "[BYPASS] OpenSSL\n[BYPASS] SChannel"))
    r = frida_bypass_pc_ssl.func("proc")
    assert "BYPASS_OK" in r
    assert "2" in r  # 2 处绕过


def test_bypass_pc_ssl_fail(monkeypatch):
    """输出仅含 Error 无 BYPASS 应返回 BYPASS_FAIL。"""
    monkeypatch.setattr(pct, "_check_binary", lambda name: "/frida")
    monkeypatch.setattr(pct, "_run_frida",
                        lambda args, timeout=30: (1, "Error: process not found"))
    r = frida_bypass_pc_ssl.func("proc")
    assert "BYPASS_FAIL" in r


def test_bypass_pc_ssl_script_missing(monkeypatch):
    """脚本路径指向不存在文件应返回 BYPASS_FAIL + 缺失。"""
    monkeypatch.setattr(pct, "_check_binary", lambda name: "/frida")
    monkeypatch.setattr(pct, "_pc_ssl_bypass_js_path", lambda: Path("/nonexistent/pc_ssl.js"))
    r = frida_bypass_pc_ssl.func("proc")
    assert "BYPASS_FAIL" in r and "缺失" in r


# ---- system.md 一致性 ----

def test_system_md_tool_count_49():
    """system.md 工具清单应有 49 条（1-49）。"""
    p = Path(__file__).resolve().parents[1] / "crawagent" / "prompts" / "system.md"
    content = p.read_text(encoding="utf-8")
    # 统计 "数字. 工具名(" 模式（工具清单条目）
    import re
    entries = re.findall(r"^\d+\.\s+\w+\(", content, re.MULTILINE)
    assert len(entries) == 49, f"工具条目数应为 49，实际 {len(entries)}"


def test_system_md_pc_workflow_steps():
    """system.md 应含 PC 逆向 Workflow 段 + Step 0-3。"""
    p = Path(__file__).resolve().parents[1] / "crawagent" / "prompts" / "system.md"
    content = p.read_text(encoding="utf-8")
    assert "PC 逆向 / Frida Hook Workflow" in content
    assert "Step 0 — 进程就位" in content
    assert "Step 1 — 定位加密函数" in content
    assert "Step 2 — 绕过证书锁定" in content
    assert "Step 3 — dump DLL" in content
    # 工具总数行应更新为 49
    assert "49 powerful tools" in content
