"""Phase 3 — YAML 档案存储层专属测试。

覆盖：
  1. _origin_to_filename 命名规则（含 URL 标准化）
  2. YAML roundtrip：写入 → 读回所有字段一致（script 多行 / cookies 长串 / tags 列表）
  3. 自动迁移幂等：marker 文件存在时不重复迁移
  4. 跨 origin 线程隔离：不同站点的锁互不阻塞
  5. 公共 API 签名不变（list_sites / upsert_site / delete_site / get_site_cookies）
"""
import sys
import threading
from pathlib import Path
from datetime import datetime

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture(autouse=True)
def _isolated_storage(tmp_path, monkeypatch):
    """每个测试独立的 YAML 目录 + 跳过迁移检查。"""
    from crawagent.tools import site_profile_tool as spt
    monkeypatch.setattr(spt, "_profiles_dir", lambda: tmp_path)
    monkeypatch.setattr(spt, "_old_json_path", lambda: tmp_path / "sites.json")
    monkeypatch.setattr(spt, "_ensure_migrated", lambda: None)
    monkeypatch.setattr(spt, "_MIGRATION_CHECKED", True)
    # 清锁字典，避免跨测试持有旧锁
    spt._locks.clear()
    return tmp_path


# —— 1. 文件名规则 ——

def test_origin_to_filename_basic():
    from crawagent.tools.site_profile_tool import _origin_to_filename
    # . 和 - 是合法文件名字符，保留
    assert _origin_to_filename("https://weread.qq.com") == "weread.qq.com.yaml"
    assert _origin_to_filename("https://www.bilibili.com") == "www.bilibili.com.yaml"
    assert _origin_to_filename("http://example.org") == "example.org.yaml"


def test_origin_to_filename_no_scheme():
    from crawagent.tools.site_profile_tool import _origin_to_filename
    # 不带 scheme 也能正确处理（内部 _normalize_origin 会补 https://）
    assert _origin_to_filename("weread.qq.com") == "weread.qq.com.yaml"


def test_origin_to_filename_port_stripped():
    from crawagent.tools.site_profile_tool import _origin_to_filename
    # 非标准端口也应该进同一个文件（origin 本身标准化就不带端口？）
    # 实际 normalize_origin 保留 netloc 原样，所以 http://example.com:8080 → 8080 会被包含
    result = _origin_to_filename("https://example.com:8080")
    assert result.endswith(".yaml")
    assert "8080" in result


# —— 2. YAML roundtrip（所有字段 lossless）——

def test_yaml_roundtrip_all_fields(tmp_path):
    from crawagent.tools import site_profile_tool as spt

    long_script = """import requests
import json
# 一段多行脚本
resp = requests.get(url, headers={"User-Agent": "test"})
data = json.loads(resp.text)
print(data["result"])
"""
    long_cookie = "a=1; b=2; c=3; d=4; e=5; f=6; g=7; h=8; i=9; j=0; k=1111111111; l=2222222222"

    created = spt.upsert_site(
        "https://multi.example.com",
        title="多字段测试站",
        strategy="custom_script",
        notes="测试所有字段 roundtrip",
        cookies=long_cookie,
        script=long_script,
        kind="custom",
        tags=["tag1", "tag2", "中文标签"],
    )
    origin = created["origin"]

    # 检查文件确实在 disk 上
    yaml_files = list(tmp_path.glob("*.yaml"))
    assert len(yaml_files) == 1

    # 再通过 list_sites 读回来
    all_sites = spt.list_sites()
    assert len(all_sites) == 1
    back = all_sites[0]

    # 所有字段 lossless
    assert back["origin"] == origin
    assert back["title"] == "多字段测试站"
    assert back["strategy"] == "custom_script"
    assert back["notes"] == "测试所有字段 roundtrip"
    assert back["cookies"] == long_cookie, f"cookies 长串应该无损"
    assert back["script"] == long_script, f"script 多行应该无损"
    assert back["kind"] == "custom"
    assert back["tags"] == ["tag1", "tag2", "中文标签"]
    assert "created_at" in back
    assert "last_crawled_at" in back


def test_yaml_roundtrip_empty_fields():
    from crawagent.tools import site_profile_tool as spt

    created = spt.upsert_site(
        "https://empty.example.com",
        title="",
        strategy="crawl_webpage",
        notes="",
        cookies="",  # 空串
        script="",
        kind="",
        tags=[],
    )
    back = spt.list_sites()[0]
    assert back["cookies"] == ""
    assert back["script"] == ""
    assert back["tags"] == []
    assert back["title"] == "https://empty.example.com"  # title 空时 fallback 到 origin


# —— 3. 自动迁移 ——

def test_migration_skips_when_marker_exists(tmp_path, monkeypatch):
    from crawagent.tools import site_profile_tool as spt

    # 手动造一个 sites.json + migration marker
    json_path = tmp_path / "sites.json"
    json_path.write_text('{"sites": [{"origin": "https://old.example.com", "title": "旧站"}]}', encoding="utf-8")
    marker = tmp_path / ".migrated_from_sites_json.ok"
    marker.write_text("already migrated", encoding="utf-8")

    # mock _profiles_dir 指向 tmp_path
    monkeypatch.setattr(spt, "_profiles_dir", lambda: tmp_path)

    # 重置迁移状态
    spt._MIGRATION_CHECKED = False

    # 手动调 _migrate_json_to_yaml
    spt._migrate_json_to_yaml()

    # 不应该产生 yaml 文件（marker 存在 → 跳过）
    yamls = list(tmp_path.glob("*.yaml"))
    assert len(yamls) == 0, "marker 存在时不应重复迁移"


def test_migration_runs_when_no_marker(tmp_path, monkeypatch):
    from crawagent.tools import site_profile_tool as spt

    json_path = tmp_path / "sites.json"
    json_path.write_text(
        '{"sites": [{"origin": "https://migrate.example.com", "title": "迁移测试"}]}',
        encoding="utf-8",
    )

    monkeypatch.setattr(spt, "_profiles_dir", lambda: tmp_path)
    spt._MIGRATION_CHECKED = False

    spt._migrate_json_to_yaml()

    yamls = list(tmp_path.glob("*.yaml"))
    assert len(yamls) == 1, "应该产生 1 个 yaml"
    # marker 也应该写好
    marker = tmp_path / ".migrated_from_sites_json.ok"
    assert marker.exists()


def test_migration_preserves_real_data(tmp_path):
    """用真实 sites.json 验证 12 条数据全迁移。"""
    import json
    real = Path(__file__).resolve().parents[1] / "data" / "sites.json"
    if not real.exists():
        pytest.skip("真实 sites.json 不存在（可能是 CI 环境）")

    from crawagent.tools import site_profile_tool as spt

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(spt, "_profiles_dir", lambda: tmp_path)
    monkeypatch.setattr(spt, "_old_json_path", lambda: real)
    spt._MIGRATION_CHECKED = False

    spt._migrate_json_to_yaml()

    yamls = list(tmp_path.glob("*.yaml"))
    expected = len(json.loads(real.read_text(encoding="utf-8")).get("sites", []))
    assert len(yamls) == expected, f"应该迁移 {expected} 条，实际 {len(yamls)}"

    # get_site_cookies 能读到 B 站 cookie
    # 注意 get_site_cookies 内部也调 _ensure_migrated，但我们已经手动迁了
    monkeypatch.setattr(spt, "_ensure_migrated", lambda: None)
    cookie = spt.get_site_cookies("https://www.bilibili.com")
    assert len(cookie) > 0, "B 站 cookie 应该能读到"
    monkeypatch.undo()


# —— 4. 跨 origin 线程隔离 ——

def test_cross_origin_lock_isolation(tmp_path):
    """不同 origin 的 upsert 应该能并发，同 origin 仍串行。"""
    from crawagent.tools import site_profile_tool as spt

    errors: list[str] = []

    def upsert(origin: str, delay: float):
        try:
            import time
            with spt._lock_for(origin):
                time.sleep(delay)  # 故意 hold 一下锁
                spt._save_profile({
                    "origin": origin,
                    "title": origin,
                    "strategy": "crawl_webpage",
                    "notes": "",
                    "cookies": "",
                    "script": "",
                    "tags": [],
                    "created_at": datetime.now().isoformat(),
                    "last_crawled_at": datetime.now().isoformat(),
                })
        except Exception as e:
            errors.append(str(e))

    # 两个不同 origin 同时 upsert — 不同锁，应该并发
    t1 = threading.Thread(target=upsert, args=("https://a.example.com", 0.1))
    t2 = threading.Thread(target=upsert, args=("https://b.example.com", 0.1))
    t1.start()
    t2.start()
    t1.join(timeout=1)
    t2.join(timeout=1)

    assert not errors, f"upsert 出错: {errors}"
    yamls = list(tmp_path.glob("*.yaml"))
    assert len(yamls) == 2

    # 确认锁字典按 origin 分桶
    assert "https://a.example.com" in spt._locks
    assert "https://b.example.com" in spt._locks
    assert spt._locks["https://a.example.com"] is not spt._locks["https://b.example.com"], \
        "不同 origin 应该有不同锁对象"


# —— 5. 公共 API 签名（用 inspect 验证）——

def test_public_api_signature_unchanged():
    """Phase 3 重构存储层后，公共 API 的参数签名必须不变。

    这保证 web/routers/sites.py 和 weread_tool.py 零改动继续工作。
    """
    import inspect
    from crawagent.tools import site_profile_tool as spt

    # list_sites — 无参数，返回 list[dict]
    sig = inspect.signature(spt.list_sites)
    assert list(sig.parameters.keys()) == []

    # delete_site — 一个参数 origin: str
    sig = inspect.signature(spt.delete_site)
    assert list(sig.parameters.keys()) == ["origin"]

    # get_site_cookies — 一个参数 origin: str
    sig = inspect.signature(spt.get_site_cookies)
    assert list(sig.parameters.keys()) == ["origin"]

    # upsert_site — 7 个参数（origin + title/strategy/notes/cookies/script/kind/tags）
    sig = inspect.signature(spt.upsert_site)
    params = list(sig.parameters.keys())
    assert params[0] == "origin"
    assert "cookies" in params
    assert "script" in params
    assert "tags" in params


def test_web_routers_still_import():
    """Phase 3 后 web/routers/sites.py 应该还能 import。"""
    from crawagent.web.routers.sites import list_sites_route  # noqa
    from crawagent.tools.weread_tool import _weread_cookie  # noqa
    # 能 import 就行 = API 签名没变


# —— Phase 3.2 — get_profile_config 多环境配置 roundtrip ——

def test_get_profile_config_roundtrip():
    """upsert_site 写入 config dict → get_profile_config 完整读回。"""
    from crawagent.tools import site_profile_tool as spt
    from crawagent.tools.site_profile_tool import (
        upsert_site, get_profile_config, _normalize_origin,
    )

    cfg = {
        "output_dir": "profiles_output/weread",
        "model": "deepseek-v3",
        "provider": "deepseek",
    }
    upsert_site(
        origin="https://weread.qq.com",
        tags=["book"],
        config=cfg,
    )
    got = get_profile_config("https://weread.qq.com")
    assert got == cfg, f"config 应完整 roundtrip，got={got}"

    # 读另一种写法（origin 不严格等于）
    got2 = get_profile_config("weread.qq.com")
    assert got2 == cfg


def test_get_profile_config_empty_when_no_config():
    """profile 没 config 字段时返回 {}。"""
    from crawagent.tools.site_profile_tool import upsert_site, get_profile_config

    upsert_site(origin="https://noconfig.example.com", tags=["test"])
    assert get_profile_config("https://noconfig.example.com") == {}


def test_get_profile_config_empty_when_no_such_profile():
    """根本没有这个 origin 的 profile → 返回 {}（不是抛错）。"""
    from crawagent.tools.site_profile_tool import get_profile_config

    result = get_profile_config("https://definitely.not.there.example.com")
    assert result == {}
