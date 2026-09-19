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

def _isolate_binary_lookup(monkeypatch, which_result):
    """把三级查找全部钉死：which 返回 which_result，显式/常见位置层置空，清缓存。"""
    monkeypatch.setattr(art.shutil, "which", lambda name: which_result)
    monkeypatch.setattr(art, "_settings_binary_override", lambda name: "")
    monkeypatch.setattr(art, "_common_location_binary", lambda name: "")
    art._BINARY_PATH_CACHE.pop("adb", None)
    art._BINARY_PATH_CACHE.pop("frida", None)
    art._BINARY_PATH_CACHE.pop("frida-trace", None)


def test_check_binary_missing(monkeypatch):
    _isolate_binary_lookup(monkeypatch, None)
    try:
        assert _check_binary("adb") is None
    finally:
        art._BINARY_PATH_CACHE.pop("adb", None)


def test_check_binary_found(monkeypatch):
    _isolate_binary_lookup(monkeypatch, "/usr/bin/adb")
    try:
        assert _check_binary("adb") == "/usr/bin/adb"
    finally:
        art._BINARY_PATH_CACHE.pop("adb", None)


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


# ---- 015: 三级查找（PATH → settings 显式 → 常见位置）----

def _clear_cache():
    for k in ("adb", "frida", "frida-trace"):
        art._BINARY_PATH_CACHE.pop(k, None)


def test_settings_binary_override_hits_existing_file(tmp_path, monkeypatch):
    fake = tmp_path / "my_adb.exe"
    fake.write_text("", encoding="utf-8")

    class _S:
        adb_path = str(fake)
        frida_path = ""

    monkeypatch.setattr(art, "get_settings", lambda: _S())
    try:
        assert art._settings_binary_override("adb") == str(fake)
    finally:
        _clear_cache()


def test_settings_binary_override_ignores_missing_path(tmp_path, monkeypatch):
    class _S:
        adb_path = str(tmp_path / "ghost.exe")
        frida_path = ""

    monkeypatch.setattr("crawagent.config.settings.get_settings", lambda: _S())
    assert art._settings_binary_override("adb") == ""


def test_settings_binary_override_unknown_name():
    assert art._settings_binary_override("notatool") == ""


def test_common_location_binary_hits_table(tmp_path, monkeypatch):
    fake = tmp_path / "adb.exe"
    fake.write_text("", encoding="utf-8")
    monkeypatch.setattr(art, "_ADB_COMMON_LOCATIONS", (str(fake),))
    try:
        assert art._common_location_binary("adb") == str(fake)
        assert art._common_location_binary("frida") == ""  # 表只给 adb
    finally:
        _clear_cache()


def test_check_binary_uses_settings_fallback(tmp_path, monkeypatch):
    fake = tmp_path / "sdk_adb.exe"
    fake.write_text("", encoding="utf-8")

    class _S:
        adb_path = str(fake)
        frida_path = ""

    monkeypatch.setattr(art.shutil, "which", lambda name: None)
    monkeypatch.setattr(art, "get_settings", lambda: _S())
    monkeypatch.setattr(art, "_common_location_binary", lambda name: "")
    _clear_cache()
    try:
        assert _check_binary("adb") == str(fake)
        # 缓存生效：二次调用不再查 which（把 which 改成爆炸来证明）
        monkeypatch.setattr(art.shutil, "which", lambda name: (_ for _ in ()).throw(AssertionError("should not re-query")))
        assert _check_binary("adb") == str(fake)
    finally:
        _clear_cache()


def test_check_binary_miss_reports_adb_path_hint(monkeypatch):
    _isolate_binary_lookup(monkeypatch, None)
    try:
        assert _check_binary("adb") is None
        r = list_adb_devices.func()
        assert "ADB_PATH" in r, r
    finally:
        _clear_cache()


def test_list_adb_devices_via_common_location(tmp_path, monkeypatch):
    """端到端：PATH 无 adb，常见位置兜底命中 → 成功列出设备。"""
    fake = tmp_path / "adb.exe"
    fake.write_text("", encoding="utf-8")
    monkeypatch.setattr(art.shutil, "which", lambda name: None)
    monkeypatch.setattr(art, "_settings_binary_override", lambda name: "")
    monkeypatch.setattr(art, "_common_location_binary", lambda name: str(fake))
    monkeypatch.setattr(
        art, "_run_adb",
        lambda args, device_id="", timeout=30: (
            0,
            "List of devices attached\nFAKESERIAL\tdevice product:PD2344 model:V2344A\n",
        ),
    )
    _clear_cache()
    try:
        r = list_adb_devices.func()
        assert "找到 1 台设备" in r and "FAKESERIAL" in r
    finally:
        _clear_cache()


# ---- 回归：三级查找解析出的绝对路径必须真正用于执行（015 端到端踩坑）----

def test_run_adb_uses_resolved_path(monkeypatch):
    """PATH 无 adb、靠常见位置表命中时，subprocess 收到的应是解析出的绝对路径。"""
    monkeypatch.setattr(art, "_check_binary", lambda name: r"C:\fake\platform-tools\adb.exe")
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        class R:
            returncode = 0
            stdout = "ok"
            stderr = ""
        return R()

    monkeypatch.setattr(art.subprocess, "run", fake_run)
    rc, out = art._run_adb(["devices", "-l"], timeout=5)
    assert rc == 0
    assert captured["cmd"][0] == r"C:\fake\platform-tools\adb.exe"
    assert captured["cmd"][1:] == ["devices", "-l"]


def test_run_frida_uses_resolved_path(monkeypatch):
    monkeypatch.setattr(art, "_check_binary", lambda name: r"C:\fake\frida.exe")
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        class R:
            returncode = 0
            stdout = "ok"
            stderr = ""
        return R()

    monkeypatch.setattr(art.subprocess, "run", fake_run)
    rc, out = art._run_frida(["frida", "-U", "-l", "hook.js"], timeout=5)
    assert rc == 0
    assert captured["cmd"][0] == r"C:\fake\frida.exe"
    assert captured["cmd"][1:] == ["-U", "-l", "hook.js"]


def test_list_adb_devices_skips_daemon_lines(monkeypatch):
    """adb 首启的 "* daemon started successfully" 提示行不该被算成设备。"""
    monkeypatch.setattr(art, "_check_binary", lambda name: "/adb")
    monkeypatch.setattr(
        art, "_run_adb",
        lambda args, device_id="", timeout=10: (
            0,
            "* daemon not running; starting now at tcp:5037\n"
            "* daemon started successfully\n"
            "List of devices attached\n",
        ),
    )
    assert "NO_DEVICE" in list_adb_devices.func()
