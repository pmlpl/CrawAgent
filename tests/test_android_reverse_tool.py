"""Android 逆向工具测试 — 全离线，mock shutil.which / _run_adb / _run_frida，不真连设备。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from crawagent.tools import android_reverse_tool as art
from crawagent.tools.android_reverse_tool import (
    list_adb_devices, install_apk, push_file, frida_hook_function,
    frida_dump_so, frida_bypass_ssl_pinning,
    _check_binary, _truncate,
)


# ---- _check_binary ----

def test_check_binary_missing(monkeypatch):
    monkeypatch.setattr(art.shutil, "which", lambda name: None)
    assert _check_binary("adb") is None


def test_check_binary_found(monkeypatch):
    monkeypatch.setattr(art.shutil, "which", lambda name: "/usr/bin/adb")
    assert _check_binary("adb") == "/usr/bin/adb"


# ---- _truncate ----

def test_truncate_short():
    assert _truncate("abc") == "abc"


def test_truncate_long():
    out = _truncate("x" * 5000, limit=100)
    assert len(out) < 5000 and "截断" in out


# ---- list_adb_devices ----

def test_list_adb_devices_no_adb(monkeypatch):
    monkeypatch.setattr(art, "_check_binary", lambda name: None)
    r = list_adb_devices.func()
    assert "ERR" in r and "adb 未安装" in r


def test_list_adb_devices_no_device(monkeypatch):
    monkeypatch.setattr(art, "_check_binary", lambda name: "/adb")
    monkeypatch.setattr(art, "_run_adb",
                        lambda args, device_id="", timeout=30: (0, "List of devices attached\n"))
    r = list_adb_devices.func()
    assert "NO_DEVICE" in r


def test_list_adb_devices_found(monkeypatch):
    monkeypatch.setattr(art, "_check_binary", lambda name: "/adb")
    def fake_run(args, device_id="", timeout=30):
        if "devices" in args:
            return (0, "List of devices attached\nemulator-5554 device model:Pixel_5\n")
        if "getprop" in args:
            return (0, "13\n")
        return (0, "")
    monkeypatch.setattr(art, "_run_adb", fake_run)
    r = list_adb_devices.func()
    assert "找到 1 台设备" in r
    assert "emulator-5554" in r and "Pixel_5" in r and "android=13" in r


# ---- install_apk ----

def test_install_apk_no_adb(monkeypatch):
    monkeypatch.setattr(art, "_check_binary", lambda name: None)
    assert "adb 未安装" in install_apk.func("dev", "x.apk")


def test_install_apk_missing_file(tmp_path, monkeypatch):
    monkeypatch.setattr(art, "_check_binary", lambda name: "/adb")
    r = install_apk.func("dev", str(tmp_path / "nope.apk"))
    assert "ERR" in r and "不存在" in r


def test_install_apk_success(tmp_path, monkeypatch):
    monkeypatch.setattr(art, "_check_binary", lambda name: "/adb")
    apk = tmp_path / "app.apk"
    apk.write_bytes(b"fake apk")
    monkeypatch.setattr(art, "_run_adb", lambda args, device_id="", timeout=120: (0, "Success"))
    r = install_apk.func("dev", str(apk))
    assert "OK" in r and "安装成功" in r


def test_install_apk_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(art, "_check_binary", lambda name: "/adb")
    apk = tmp_path / "app.apk"
    apk.write_bytes(b"fake")
    monkeypatch.setattr(art, "_run_adb",
                        lambda args, device_id="", timeout=120: (1, "Failure [INSTALL_FAILED]"))
    r = install_apk.func("dev", str(apk))
    assert "FAIL" in r


# ---- push_file ----

def test_push_file_missing_local(tmp_path, monkeypatch):
    monkeypatch.setattr(art, "_check_binary", lambda name: "/adb")
    r = push_file.func("dev", str(tmp_path / "nope.bin"), "/data/local/tmp/x")
    assert "ERR" in r and "不存在" in r


def test_push_file_success(tmp_path, monkeypatch):
    monkeypatch.setattr(art, "_check_binary", lambda name: "/adb")
    f = tmp_path / "frida-server"
    f.write_bytes(b"bin")
    monkeypatch.setattr(art, "_run_adb", lambda args, device_id="", timeout=60: (0, "pushed"))
    r = push_file.func("dev", str(f), "/data/local/tmp/frida-server")
    assert "OK" in r and "已推送" in r


# ---- frida_hook_function ----

def test_frida_hook_no_frida_trace(monkeypatch):
    monkeypatch.setattr(art, "_check_binary", lambda name: None)
    r = frida_hook_function.func("dev", "com.x", "com.xx.Util.sign")
    assert "ERR" in r and "frida-trace 未安装" in r


def test_frida_hook_java_pattern_conversion(monkeypatch):
    """com.xx.Util.sign 应转成 com.xx.Util!sign 并用 -j flag。"""
    monkeypatch.setattr(art, "_check_binary", lambda name: "/frida-trace")
    captured = {}
    def fake_run(args, timeout=30):
        captured["args"] = args
        return (0, "instrumented")
    monkeypatch.setattr(art, "_run_frida", fake_run)
    r = frida_hook_function.func("dev", "com.x", "com.xx.Util.sign")
    assert "java=True" in r
    assert "-j" in captured["args"]
    assert "com.xx.Util!sign" in captured["args"]


def test_frida_hook_native_pattern(monkeypatch):
    monkeypatch.setattr(art, "_check_binary", lambda name: "/frida-trace")
    captured = {}
    def fake_run(args, timeout=30):
        captured["args"] = args
        return (0, "ok")
    monkeypatch.setattr(art, "_run_frida", fake_run)
    r = frida_hook_function.func("dev", "com.x", "*crypt*")
    assert "java=False" in r
    assert "-i" in captured["args"]


# ---- frida_dump_so ----

def test_frida_dump_so_no_frida(monkeypatch):
    monkeypatch.setattr(art, "_check_binary", lambda name: None)
    r = frida_dump_so.func("dev", "com.x", "libnative.so")
    assert "ERR" in r and "frida 未安装" in r


def test_frida_dump_so_success(monkeypatch):
    monkeypatch.setattr(art, "_check_binary", lambda name: "/frida")
    monkeypatch.setattr(art, "_run_frida",
                        lambda args, timeout=30: (0, "BASE: 0x1234\nEXPORT: foo @ 0x5678"))
    r = frida_dump_so.func("dev", "com.x", "libnative.so")
    assert "BASE:" in r


def test_frida_dump_so_js_template_substitution(monkeypatch):
    """JS 模板 __SO_NAME__ 占位符应被替换进 frida -e 参数。"""
    monkeypatch.setattr(art, "_check_binary", lambda name: "/frida")
    captured = {}
    def fake_run(args, timeout=30):
        captured["js"] = args[-1]  # -e 后的 JS
        return (0, "BASE: 0x1")
    monkeypatch.setattr(art, "_run_frida", fake_run)
    frida_dump_so.func("dev", "com.x", "libnative.so")
    assert "libnative.so" in captured["js"]
    assert "__SO_NAME__" not in captured["js"]


def test_frida_dump_so_fail(monkeypatch):
    monkeypatch.setattr(art, "_check_binary", lambda name: "/frida")
    monkeypatch.setattr(art, "_run_frida", lambda args, timeout=30: (1, "Error: so not loaded"))
    r = frida_dump_so.func("dev", "com.x", "libnative.so")
    assert "DUMP_FAIL" in r


# ---- frida_bypass_ssl_pinning ----

def test_bypass_ssl_no_frida(monkeypatch):
    monkeypatch.setattr(art, "_check_binary", lambda name: None)
    r = frida_bypass_ssl_pinning.func("dev", "com.x")
    assert "ERR" in r and "frida 未安装" in r


def test_bypass_ssl_script_missing(monkeypatch):
    monkeypatch.setattr(art, "_check_binary", lambda name: "/frida")
    monkeypatch.setattr(art, "_ssl_pinning_js_path", lambda: Path("/nonexistent/ssl.js"))
    r = frida_bypass_ssl_pinning.func("dev", "com.x")
    assert "PINNING_FAIL" in r and "缺失" in r


def test_bypass_ssl_success(monkeypatch):
    monkeypatch.setattr(art, "_check_binary", lambda name: "/frida")
    monkeypatch.setattr(art, "_run_frida",
                        lambda args, timeout=30: (0, "[BYPASS] OkHttp3\n[BYPASS] TrustManager"))
    r = frida_bypass_ssl_pinning.func("dev", "com.x")
    assert "PINNING_OK" in r and "2" in r  # 2 处绕过


def test_bypass_ssl_fail(monkeypatch):
    monkeypatch.setattr(art, "_check_binary", lambda name: "/frida")
    monkeypatch.setattr(art, "_run_frida", lambda args, timeout=30: (1, "Error: script failed"))
    r = frida_bypass_ssl_pinning.func("dev", "com.x")
    assert "PINNING_FAIL" in r
