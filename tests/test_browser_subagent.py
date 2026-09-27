"""浏览器子 Agent（032）：LLM 三档优先级 + register_new_step_callback 步进里程碑。

LLM 三档测试全程不起浏览器/不发网络：Browser/Agent/ChatOpenAILike 全 mock，
Agent 构造即抛 RuntimeError 截停，从捕获的 kwargs 断言各档取舍。
环境隔离照 test_settings_langsmith.py：Settings.model_config.env_file 钉到临时文件
+ 清 os.environ 的 BROWSER_USE_LLM_*（pydantic env vars 优先于 .env）。
"""
import pytest

from crawagent.config.settings import Settings
from crawagent.tools.advanced_tools import _on_browser_step, browser_use_navigate

MAIN_PROVIDERS = (
    'LLM_PROVIDERS=[{"name":"主商","base_url":"http://main.example/v1",'
    '"api_key":"sk-main","models":["main-model"]}]'
)


class _Env:
    """fixture 容器：临时 .env 路径 + _ensure_no_proxy_for 调用记录。"""

    def __init__(self, env_file, no_proxy_calls):
        self.env_file = env_file
        self.no_proxy_calls = no_proxy_calls

    def write(self, text):
        self.env_file.write_text(text, encoding="utf-8")


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """临时 .env（含主 provider）+ 清 BROWSER_USE_LLM_* 环境变量 + NO_PROXY 记录器。"""
    env_file = tmp_path / "env"
    monkeypatch.setitem(Settings.model_config, "env_file", env_file)
    for k in ("BROWSER_USE_LLM_MODEL", "BROWSER_USE_LLM_BASE_URL", "BROWSER_USE_LLM_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    calls: list[str] = []
    monkeypatch.setattr("crawagent.llm.model._ensure_no_proxy_for", lambda base: calls.append(base))
    yield _Env(env_file, calls)


class _CaptureAgent:
    """构造即截停：捕获 kwargs 后抛错，工具兜成 [ERROR]，不真起浏览器。"""
    last_kwargs: dict | None = None

    def __init__(self, **kw):
        type(self).last_kwargs = kw
        raise RuntimeError("stop-here")


@pytest.fixture()
def mocked(monkeypatch):
    import browser_use
    monkeypatch.setattr(browser_use, "Agent", _CaptureAgent)
    monkeypatch.setattr(browser_use, "Browser", lambda *a, **kw: object())

    class _FakeLLM:
        last = None

        def __init__(self, model="", api_key="", base_url=""):
            self.model, self.api_key, self.base_url = model, api_key, base_url
            type(self).last = self

    monkeypatch.setattr("browser_use.llm.openai.like.ChatOpenAILike", _FakeLLM)
    _CaptureAgent.last_kwargs = None
    return _FakeLLM


def _run_tool():
    res = browser_use_navigate.func("https://example.com", "测试")
    assert "[ERROR]" in res and "stop-here" in res  # 截停被兜住，不是别的错
    return _CaptureAgent.last_kwargs


def test_tier1_settings_model_reuses_main_provider(env, mocked):
    """设置页只填模型名 → model 用该值，base_url/api_key 回落主 provider。"""
    env.write(MAIN_PROVIDERS + "\nBROWSER_USE_LLM_MODEL=glm-4v-flash\n")
    llm = _run_tool()["llm"]
    assert llm.model == "glm-4v-flash"
    assert llm.base_url == "http://main.example/v1"
    assert llm.api_key == "sk-main"


def test_tier1_settings_full_overrides(env, mocked):
    """设置页三键全填 → 全用设置值，不碰主 provider。"""
    env.write(
        MAIN_PROVIDERS + "\n"
        "BROWSER_USE_LLM_MODEL=glm-4v-flash\n"
        "BROWSER_USE_LLM_BASE_URL=http://sub.example/v1\n"
        "BROWSER_USE_LLM_API_KEY=sk-sub\n"
    )
    llm = _run_tool()["llm"]
    assert llm.model == "glm-4v-flash"
    assert llm.base_url == "http://sub.example/v1"
    assert llm.api_key == "sk-sub"


def test_env_passthrough_merges_into_settings(env, mocked, monkeypatch):
    """os.environ 的 BROWSER_USE_LLM_* 经 pydantic 直接成为 Settings 字段：
    .env 配 model、os.environ 配 key → 同名合并生效（旧手写 .env 用户无缝迁入 tier1）。"""
    env.write(MAIN_PROVIDERS + "\nBROWSER_USE_LLM_MODEL=glm-4v-flash\n")
    monkeypatch.setenv("BROWSER_USE_LLM_API_KEY", "sk-env")
    llm = _run_tool()["llm"]
    assert llm.model == "glm-4v-flash"
    assert llm.api_key == "sk-env"


def test_tier2_env_key_fallback(env, mocked, monkeypatch):
    """settings 空 + env 有独立 key → 用 env 三元组。"""
    env.write(MAIN_PROVIDERS + "\n")
    monkeypatch.setenv("BROWSER_USE_LLM_API_KEY", "sk-env")
    monkeypatch.setenv("BROWSER_USE_LLM_BASE_URL", "http://env.example/v1")
    monkeypatch.setenv("BROWSER_USE_LLM_MODEL", "gpt-4o-mini")
    llm = _run_tool()["llm"]
    assert llm.model == "gpt-4o-mini"
    assert llm.api_key == "sk-env"
    assert llm.base_url == "http://env.example/v1"


def test_tier2_env_key_default_openai_base(env, mocked, monkeypatch):
    """env 只给 key 不给 base_url → 默认 api.openai.com + 默认模型。"""
    env.write(MAIN_PROVIDERS + "\n")
    monkeypatch.setenv("BROWSER_USE_LLM_API_KEY", "sk-env")
    llm = _run_tool()["llm"]
    assert llm.base_url == "https://api.openai.com/v1"
    assert llm.model == "gpt-4o-mini"


def test_tier3_main_model_fallback(env, mocked):
    """settings 与 env 全空 → 主 LLM（provider 首模型）。"""
    env.write(MAIN_PROVIDERS + "\n")
    llm = _run_tool()["llm"]
    assert llm.model == "main-model"
    assert llm.base_url == "http://main.example/v1"
    assert llm.api_key == "sk-main"


def test_no_proxy_called_with_final_base(env, mocked):
    """_ensure_no_proxy_for 对最终 base_url 照常调用（tier1 base 留空回落主 provider）。"""
    env.write(MAIN_PROVIDERS + "\nBROWSER_USE_LLM_MODEL=glm-4v-flash\n")
    _run_tool()
    assert env.no_proxy_calls == ["http://main.example/v1"]


def test_agent_receives_step_callback(env, mocked):
    """Agent 构造带上 register_new_step_callback=_on_browser_step。"""
    env.write(MAIN_PROVIDERS + "\n")
    kwargs = _run_tool()
    assert kwargs["register_new_step_callback"] is _on_browser_step
    assert kwargs["task"].startswith("打开 https://example.com")


# ── 033：四档挑选（手配 > env > 池中两维全过 > 主模型）──

class _OkAgent:
    """成功路径：run() 返回可取的 final_result，让工具走到返回头标注。"""
    last_kwargs: dict | None = None

    def __init__(self, **kw):
        type(self).last_kwargs = kw

    async def run(self):
        return SimpleNamespace(final_result=lambda: "页面内容", extracted_content=None)


POOL_PROVIDERS = (
    'LLM_PROVIDERS=[{"name":"主商","base_url":"http://main.example/v1","api_key":"sk-main","models":["main-model"]},'
    '{"name":"视觉商","base_url":"http://vision.example/v1","api_key":"sk-vision",'
    '"models":["glm-4v-flash","text-only"],'
    '"caps":{'
    '"glm-4v-flash":{"vision":true,"structured":true,"vision_reply":"青色","structured_reply":"{}"},'
    '"text-only":{"vision":true,"structured":false,"vision_reply":"青色","structured_reply":"400"}}}]'
)


def test_tier3_pool_picks_first_both_dims_passed(env, mocked):
    """池中第一个两维全过（vision+structured 显式 True）的模型被自动选中。"""
    env.write(POOL_PROVIDERS)
    llm = _run_tool()["llm"]
    assert llm.model == "glm-4v-flash"
    assert llm.base_url == "http://vision.example/v1"
    assert llm.api_key == "sk-vision"
    assert env.no_proxy_calls == ["http://vision.example/v1"]


def test_tier3_structured_false_blocks_vision_model(env, mocked):
    """结构化不过的模型即使视觉过也不自动选（比盲跑更糟）→ 回落主模型。"""
    env.write(
        'LLM_PROVIDERS=[{"name":"主商","base_url":"http://main.example/v1","api_key":"sk-main","models":["main-model"]},'
        '{"name":"视觉商","base_url":"http://vision.example/v1","api_key":"sk-vision","models":["text-only"],'
        '"caps":{"text-only":{"vision":true,"structured":false}}}]'
    )
    llm = _run_tool()["llm"]
    assert llm.model == "main-model"
    assert llm.base_url == "http://main.example/v1"


def test_tier3_no_qualified_falls_back_to_main(env, mocked):
    """池内无两维全过 → 主模型，行为与 032 一致。"""
    env.write(MAIN_PROVIDERS + "\n")
    llm = _run_tool()["llm"]
    assert llm.model == "main-model"


def test_tier1_settings_beats_pool(env, mocked):
    """手配三字段永远优先于池中自动挑选。"""
    env.write(POOL_PROVIDERS + "\nBROWSER_USE_LLM_MODEL=glm-4v-flash\n")
    llm = _run_tool()["llm"]
    assert llm.model == "glm-4v-flash"
    assert llm.base_url == "http://main.example/v1"  # base 留空回落主 provider（032 语义）


def test_pattern_guess_alone_does_not_qualify(env, mocked):
    """pattern 推测视觉（structured 未验证）不算两维全过 → 不自动选。"""
    env.write(
        'LLM_PROVIDERS=[{"name":"主商","base_url":"http://main.example/v1","api_key":"sk-main","models":["main-model"]},'
        '{"name":"视觉商","base_url":"http://vision.example/v1","api_key":"sk-vision","models":["gpt-4o-mini"]}]'
    )
    llm = _run_tool()["llm"]
    assert llm.model == "main-model"  # gpt-4o-mini 命中 pattern 但 structured=None


def test_pick_pool_direct(env):
    """_pick_pool_vision_llm 直测：空池返回 None；合格返回三元组。"""
    from crawagent.tools.advanced_tools import _pick_pool_vision_llm
    env.write("LLM_PROVIDERS=[]\n")
    assert _pick_pool_vision_llm() is None
    env.write(POOL_PROVIDERS)
    assert _pick_pool_vision_llm() == ("glm-4v-flash", "http://vision.example/v1", "sk-vision")


def test_success_result_carries_model_header(env, monkeypatch):
    """成功路径返回头带 [子Agent模型: ...] 标注（池选标 vision）。"""
    import browser_use
    monkeypatch.setattr(browser_use, "Agent", _OkAgent)
    monkeypatch.setattr(browser_use, "Browser", lambda *a, **kw: object())

    class _FakeLLM:
        def __init__(self, model="", api_key="", base_url=""):
            self.model, self.api_key, self.base_url = model, api_key, base_url

    monkeypatch.setattr("browser_use.llm.openai.like.ChatOpenAILike", _FakeLLM)
    env.write(POOL_PROVIDERS)
    res = browser_use_navigate.func("https://example.com", "测试")
    assert res.startswith("[子Agent模型: glm-4v-flash (vision)]\n页面内容")


# ── 步进回调 _on_browser_step ──

from types import SimpleNamespace  # noqa: E402

from crawagent.tools.progress import _INDEX, finish_tool, start_tool  # noqa: E402


class _FakeAction:
    def __init__(self, dump):
        self._dump = dump

    def model_dump(self, exclude_unset=True):
        return self._dump


def _milestone(pid):
    return _INDEX[pid].milestones[-1] if _INDEX[pid].milestones else None


def test_on_step_reports_action_summary():
    """有动作 → "步骤 N：动作键: 参数"。"""
    pid = start_tool("browser_use_navigate", "test")
    try:
        out = SimpleNamespace(action=[_FakeAction({"click_element_by_index": {"index": 5}})])
        _on_browser_step(None, out, 3)
        assert _milestone(pid) == '步骤 3：click_element_by_index: {"index": 5}'
    finally:
        finish_tool(pid)


def test_on_step_skips_empty_actions():
    """action 空/None/只有空值键 → 退化为"步骤 N 执行中"。"""
    pid = start_tool("browser_use_navigate", "test")
    try:
        _on_browser_step(None, SimpleNamespace(action=[]), 1)
        assert _milestone(pid) == "步骤 1 执行中"
        _on_browser_step(None, None, 2)  # output=None 也不炸
        assert _milestone(pid) == "步骤 2 执行中"
        _on_browser_step(None, SimpleNamespace(action=[_FakeAction({"done": {"text": ""}})]), 4)
        assert _milestone(pid) == "步骤 4 执行中"
    finally:
        finish_tool(pid)


def test_on_step_truncates_long_summary():
    """超长动作参数截断 ~60 字。"""
    pid = start_tool("browser_use_navigate", "test")
    try:
        out = SimpleNamespace(action=[_FakeAction({"input_text": {"text": "长" * 100}})])
        _on_browser_step(None, out, 5)
        last = _milestone(pid)
        assert last is not None and last.startswith("步骤 5：input_text")
        assert len(last) <= 60 + len("步骤 5：")
    finally:
        finish_tool(pid)


def test_on_step_swallows_report_failure(monkeypatch):
    """report_progress 抛错不冒泡（进度上报失败绝不影响主流程）。

    patch 目标是 advanced_tools 的绑定（from-import 拷贝），不是 progress 模块属性。
    """
    monkeypatch.setattr(
        "crawagent.tools.advanced_tools.report_progress",
        lambda msg: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    out = SimpleNamespace(action=[_FakeAction({"navigate": {"url": "https://x"}})])
    _on_browser_step(None, out, 1)  # 不抛即过
