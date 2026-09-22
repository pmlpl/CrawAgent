# 变更 020：HTTP 请求样板合并到 `crawagent/tools/_http.py`

| 项目 | 内容 |
|------|------|
| 变更编号 | 020 |
| 提出日期 | 2026-09-20 |
| 状态 | 待批准 |
| 类型 | 重构 / 去重 |
| 关联模块 | `crawagent/tools/pagination.py` 已有 `http_get`；其余 9+ 工具直接调 `requests.get` / `requests.post` |
| 来源 | `docs/tech-debt/2026-09-20.md` P2 #8 |

---

## 〇、价值

- **改动前**：9+ 个工具文件直接调 `requests.get(url, headers=HEADERS, timeout=20)` 然后 `raise_for_status()`，模式完全相同但各自复制一遍。`pagination.py` 是唯一封装过的版本（`http_get(url, timeout=20)`），但其它工具没复用。改一处 timeout / UA / 节流策略要扫所有调用点。
- **改动后**：统一封装到 `crawagent/tools/_http.py::http_get()`（含 headers / timeout / 节流 / 错误转发）。所有调用点改用单 helper。后续调节流策略 / UA / 超时只改一处。

---

## 一、背景与问题

**重复点**（grep 出来 9+ 处）：
- `crawagent/tools/pagination.py:32-37` — `http_get`（唯一封装）
- `crawagent/tools/crawl_tool.py:55` — `requests.get(url, headers=headers, timeout=settings.request_timeout)`
- `crawagent/tools/browse_tool.py:47`
- `crawagent/tools/font_decrypt.py:52, 139`
- `crawagent/tools/login_tool.py:456`
- `crawagent/tools/proxy_tool.py:104`
- `crawagent/tools/search_tool.py:21, 54`
- `crawagent/tools/video_probe_tool.py:60, 200, 209`
- `crawagent/tools/weread_tool.py`（多个内联）
- `crawagent/tools/download_images.py:153`
- `crawagent/scheduler/cron_scheduler.py:224`

每个都是同一模板：headers / timeout / `raise_for_status` / 返回 text。

---

## 二、目标

1. 抽 `crawagent/tools/_http.py::http_get()` — 接受 url + 可选 headers + 可选 timeout，返回 str（响应 text）
2. 调用点改用 helper（10+ 处）
3. **不动**：
   - `pagination.py` 已有 `http_get` 复用为转发到 `_http.http_get`（或保留，但其内部实现委托新 helper）
   - `download_images.py` 这种用 `stream=True` 的特殊调用**不强制合并**（按需）
   - `proxy_tool._health_check` 是 TCP socket 不走 HTTP，**不在范围**

---

## 三、方案设计

### 3.1 新 helper

```python
# crawagent/tools/_http.py
"""HTTP 工具共享层 — headers / timeout / 节流策略一处生效。

pagination.py 已有同名 http_get，本次把它迁到这里统一实现；pagination 的 http_get 改为薄壳转发。
"""
from __future__ import annotations
import requests
from crawagent.tools.crawl_tool import DEFAULT_UA, _enforce_delay
from crawagent.config.settings import get_settings

DEFAULT_TIMEOUT = 20

def http_get(url: str, *, headers: dict | None = None, timeout: float = DEFAULT_TIMEOUT) -> str:
    """统一 GET：节流 + 默认 UA + 可选 timeout + 抛 HTTPError。

    与 pagination.py 旧 http_get 行为一致：返回 text，HTTP 错误抛 requests.HTTPError。
    """
    _enforce_delay()
    final_headers = {"User-Agent": DEFAULT_UA, **(headers or {})}
    resp = requests.get(url, headers=final_headers, timeout=timeout)
    resp.raise_for_status()
    return resp.text
```

### 3.2 调用点迁移

每个文件从：
```python
resp = requests.get(url, headers=headers, timeout=settings.request_timeout)
resp.raise_for_status()
return resp.text
```

改为：
```python
from crawagent.tools._http import http_get
return http_get(url, headers=headers, timeout=settings.request_timeout)
```

### 3.3 不动

- `pagination.py` 内部 `http_get` 保留（向后兼容）或改为转发到 `_http.http_get` —— 选后者，统一实现
- `proxy_tool.py` 的 TCP socket `_health_check` — 不走 HTTP
- `download_images.py` 用 `stream=True` 的特殊路径 — 不强制合并

---

## 四、涉及文件清单

| 操作 | 文件 | 说明 |
|------|------|------|
| 新增 | `crawagent/tools/_http.py` | 共享 http_get helper |
| 修改 | `crawagent/tools/pagination.py` | `http_get` 改为转发到 `_http.http_get` |
| 修改 | `crawagent/tools/crawl_tool.py` | `crawl_webpage` 内 `requests.get` → `_http.http_get` |
| 修改 | `crawagent/tools/browse_tool.py` | 同上 |
| 修改 | `crawagent/tools/font_decrypt.py` | 2 处 |
| 修改 | `crawagent/tools/login_tool.py` | 1 处 |
| 修改 | `crawagent/tools/proxy_tool.py` | `_probe_proxy` 1 处（TCP health check 不动） |
| 修改 | `crawagent/tools/search_tool.py` | 2 处 |
| 修改 | `crawagent/tools/video_probe_tool.py` | 3 处 |
| 修改 | `crawagent/tools/weread_tool.py` | 多处 |
| 修改 | `crawagent/scheduler/cron_scheduler.py` | 1 处 |

---

## 五、验证方式

1. **新增单测**：`tests/test_http_helper.py`：
   - `test_http_get_returns_text`
   - `test_http_get_passes_default_ua`
   - `test_http_get_raises_on_4xx`
   - `test_http_get_respects_timeout`
2. **现有调用点不回归**：被改的工具测试（如 `test_pagination.py` / `test_crawl_tool.py`）仍过
3. **全量 pytest**：基线 399 + ≥4 新 = ≥403 passed
4. **静态检查**：`grep -n "requests.get\|requests.post" crawagent/tools/*.py crawagent/scheduler/*.py` — 应只在 `_http.py` 出现

---

## 六、后续可扩展（不在本次范围）

- `http_post()` 同样封装
- 自动重试（429/5xx）—— `pagination.make_session()` 已部分覆盖，可整合
- 下载文件 `stream=True` helper（目前 `download_images.py` 内联）

---

## 七、实施顺序建议

1. 写 `tests/test_http_helper.py`（先 red）
2. 写 `crawagent/tools/_http.py::http_get`
3. 改 `pagination.py` 转发
4. 改其他调用点（一次改一个，跑测试）
5. 全量 pytest
6. 写 §八 实施记录

---

## 八、实施记录

**实施日期**：2026-09-20
**实施人**：Agent（指挥官 Joker 批准）
**关联提交**：本批次（018-022 + 批次 2 共 14 项）一次 commit

### 8.1 实际改动

| 序号 | 操作 | 文件 | 说明 |
|------|------|------|------|
| 1 | 新增 | `crawagent/tools/_http.py` | 共享 `http_get()` — 节流 / 默认 UA / encoding 修正 / raise_for_status / 接受 `params=` |
| 2 | 新增 | `tests/test_http_helper.py` | 10 用例覆盖 helper 所有行为 |
| 3 | 修改 | `crawagent/tools/pagination.py` | 内部 `http_get()` 转发到 `_http.http_get`（保留外部签名） |
| 4 | 修改 | `crawagent/tools/crawl_tool.py` | `crawl_webpage` 改用 helper（crawl_tool 内部延迟 import 避免循环） |
| 5 | 修改 | `crawagent/tools/search_tool.py` | `_ddg_search` / `_baidu_search` 改用 helper（带 `params=`） |
| 6 | 修改 | `crawagent/tools/video_probe_tool.py` | `_probe_video_player` 内 2 处 `requests.get(...).text` → `_http_get`（:60 stream 路径不动） |
| 7 | 修改 | `crawagent/tools/weread_tool.py` | :104 改用 helper（:119 需 Response.json() 不动） |
| 8 | 修改 | `crawagent/scheduler/cron_scheduler.py` | `_run_job_once` 改用 helper |
| 9 | 修改 | `tests/test_crawl_tool.py` | 修复 `bypass_delay` fixture + `test_crawl_enforce_delay_called` — monkeypatch 路径改成 `crawagent.tools._http._enforce_delay`（import 时绑定语义） |

### 8.2 不改的调用点（spec §三.3.3 范围外）

| 文件 | 原因 |
|------|------|
| `browse_tool.py:47` | 需要 `Response.json()` |
| `font_decrypt.py:52` | `stream=True` 大文件 |
| `font_decrypt.py:139` | 需要 `Response.content`（bytes）给 `TTFont(BytesIO(...))` |
| `login_tool.py:456` | 需要 `Response.url` 拿重定向 URL |
| `proxy_tool.py:104` | 特殊 `proxies=` + `allow_redirects=False` + 需要 `Response.status_code` |
| `video_probe_tool.py:60` | `stream=True` |
| `social_utils._get` | 需要 `Response.url` |
| `social_utils._download_to_file` | `stream=True` |
| `download_images.py:153` | `stream=True` |
| `script_tool.py:781` | 脚本运行环境内独立 |
| `_template/tools/example_tool.py:31` | 模板文件 |

### 8.3 验证结果

- **`uv run pytest tests/test_http_helper.py -v`**：10/10 用例过
- **`uv run pytest tests/test_crawl_tool.py -v`**：9/9 用例过（含修复后 `test_crawl_enforce_delay_called`）
- **`uv run pytest tests/ -q`**：410 passed, 6 warnings in 77.26s（基线 400 → 410，+10）
- **`grep -rn "requests.get\|requests.post" crawagent/tools/*.py crawagent/scheduler/*.py`**：剩余在 stream / Response / proxies 路径，符合 spec 范围外定义

### 8.4 实施经验

1. **循环 import 风险**：`_http.py` 从 `crawl_tool` import `DEFAULT_UA` + `_enforce_delay`（避免重新定义全局节流状态），crawl_tool 内部使用 `from crawagent.tools._http import http_get` 延迟 import 避开循环。这种"运行时 import 已 sys.modules 注册模块"是 Python 标准解法，但模式蔓延（多个工具都延迟 import）是坏味道——长期看应把 `DEFAULT_UA` + `_enforce_delay` + `_last_request_time` 搬进 `_http.py`（_http 是底层），让 crawl_tool 也 import _http。建议在 022 ADR 0006「registry 装饰器」讨论里加一条"helper 模块不依赖工具"原则。
2. **import 时绑定语义**：`_http._enforce_delay = crawl_tool._enforce_delay` 是 import 时绑定的函数对象引用。monkeypatch `crawl_tool._enforce_delay` 替换 crawl_tool 模块的引用，但 `_http._enforce_delay` 仍指向原对象。测试要 monkeypatch `_http` 侧（已生效于 crawl_tool）才稳。这个细节踩坑后修了 2 处（`bypass_delay` autouse fixture + `test_crawl_enforce_delay_called`）。
3. **helper 签名设计取舍**：spec §三 原设计只有 `headers` + `timeout`，实施时加 `params=` 是因为 search_tool 必须用。加 `params` 后 helper 覆盖度从 ~70% → ~85%。`Response` / `bytes content` / `stream` / `proxies` 仍是 spec 范围外，11 处调用点保留内联（已记录在 §8.2）。
4. **encoding 修正比 spec 强**：spec §三 设计代码没要 encoding 修正，但 crawl_webpage 原实现有 `resp.encoding = resp.apparent_encoding or "utf-8"` 这一步避免 latin-1 误判。helper 内部加上后 crawl_tool.py 可以直接换 helper 不丢行为，且对所有调用点都更鲁棒。

### 8.5 后续

按计划推进 021（docstring 覆盖率）。本批 14 项整体 commit（per 指挥官确认）。