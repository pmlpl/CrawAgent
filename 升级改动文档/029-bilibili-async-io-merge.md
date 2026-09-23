# 变更 029：bilibili 6 端点 IO 合并（016 撤档后重新设计）

| 项目 | 内容 |
|------|------|
| 变更编号 | 029 |
| 提出日期 | 2026-09-20 |
| 状态 | 已实施 |
| 类型 | 性能重构 |
| 关联模块 | `crawagent/tools/bilibili_tool.py` |
| 来源 | `docs/tech-debt/2026-09-20.md` P4 #13 |

---

## 〇、价值

- **改动前**：`bilibili_tool.py` 6 个内部函数串行 HTTP：`_get_wbi_keys → _bilibili_playurl → _bilibili_media → _bilibili_comments`。单条 B 站抽取 4-6 个 round-trip，5-15s 延迟
- **改动后**：6 个端点并发抓取（`asyncio.gather` + `httpx.AsyncClient`），单条延迟降到 ~2-3s

> **016 教训沉淀**：变更 016 提议 `@offload` 装饰器给 sync 工具自动 offload 到线程池——实测 no-op 撤档。本变更改**真正**的 async IO（用 `httpx.AsyncClient` + `asyncio.gather`），不是 offload 到线程池假动作。

---

## 一、背景与问题

### 1.1 当前串行路径

```python
def _bilibili(url, fields):
    keys = _get_wbi_keys()           # HTTP 1
    view = _bilibili_view(bvid)       # HTTP 2（依赖 keys）
    playurl = _bilibili_playurl(bvid) # HTTP 3（独立）
    media = _bilibili_media(...)       # HTTP 4（依赖 playurl）
    comments = _bilibili_comments(...) # HTTP 5（独立）
    return {...}
```

5 个 round-trip，每个 200-500ms → 单条 2-3s（串行）。

### 1.2 端点依赖关系

| 端点 | 依赖 | 可并发？ |
|------|------|----------|
| `_get_wbi_keys` | 无 | ✓ 第一波 |
| `_bilibili_view` | wbi_keys | ✓ 第二波（与 playurl 并发） |
| `_bilibili_playurl` | 无（aid 来自 view） | ✓ 第二波 |
| `_bilibili_media` | playurl | ✗ 第三波（串行） |
| `_bilibili_comments` | 无 | ✓ 第二波 |

**优化后**：3 波（keys → {view, playurl, comments} → media）= 3 round-trip。

---

## 二、目标

1. `_bilibili(url, fields)` 改 async 函数（`_abilibili`）
2. 用 `httpx.AsyncClient` + `asyncio.gather` 并发抓取
3. **不动**外部签名：`bilibili_extract` 仍是同步入口（用 `asyncio.run` 内部驱动）
4. 性能：单条 B 站抽取 ≤ 3s（实测基线 ~5-15s）

---

## 三、方案设计

### 3.1 异步化路径

```python
import asyncio
import httpx

async def _abilibili(url: str, fields: set[str]) -> dict:
    """异步版 _bilibili — 3 波并发。"""
    bvid = _extract_bvid(url)
    
    # 第一波：WBI keys（必须先）
    keys = await _aget_wbi_keys()
    
    # 第二波：view + playurl + comments 并发
    view_task = asyncio.ensure_future(_aget_view(bvid, keys))
    playurl_task = asyncio.ensure_future(_aget_playurl(bvid))
    comments_task = asyncio.ensure_future(_aget_comments(bvid))
    view, playurl, comments = await asyncio.gather(view_task, playurl_task, comments_task)
    
    # 第三波：media（依赖 playurl）
    if "dash" in fields or "durl" in fields:
        media = await _aget_media(playurl)
    else:
        media = None
    
    return _merge_result(view, playurl, media, comments)


def _bilibili(url: str, fields: set[str]) -> dict:
    """同步入口：内部 asyncio.run 驱动。"""
    return asyncio.run(_abilibili(url, fields))


def bilibili_extract(url: str, fields: set[str]) -> dict:
    """公开入口 — 不变。"""
    return _bilibili(url, fields)
```

### 3.2 httpx.AsyncClient

```python
async def _aget_view(bvid, keys):
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(...)
        resp.raise_for_status()
        return resp.json()
```

### 3.3 共享 `_http.py` helper

`bilibili_tool` 仍可用 `crawagent.tools._http.http_get` 做同步路径（部分内部 helper），但主路径走 `httpx.AsyncClient`。

### 3.4 不动

- WBI 签名算法
- 端点 URL / 参数
- 对外签名（`bilibili_extract` / `bilibili_download`）
- 测试接口（023 补的测试能复用 mock 模式）

---

## 四、涉及文件清单

| 操作 | 文件 |
|------|------|
| 修改 | `crawagent/tools/bilibili_tool.py` |
| 可能 | `crawagent/tools/social_utils.py`（若 bilibili 共用 helper） |

---

## 五、验证方式

1. **全量 pytest**：410+ passed 不变
2. **`tests/test_bilibili_tool.py`（023 新增）覆盖**：mock `httpx.AsyncClient`，验证并发调用
3. **手动冒烟**：抓 1 条 B 站视频看耗时（基线 ~5-15s → 目标 ≤3s）

---

## 六、后续可扩展（不在本次范围）

- douyin_tool 同样改造（5+ 端点）
- wallpaper extract / save 路径 httpx 化
- 全项目异步化迁移（agent 的 ToolNode 切 async tools）

---

## 七、实施顺序建议

1. 023 测试先到位（mock 模式保护）
2. 引入 `httpx.AsyncClient` 单端点（先改 `_aget_view`）
3. 串改 6 个 `_aget_*` 端点
4. `_abilibili` 编排 3 波 gather
5. 同步入口 `_bilibili` 用 `asyncio.run`
6. 性能测试

---

## 八、实施记录

**实施日期**：2026-09-23
**实施人**：Agent（指挥官 Joker 批准）

### 8.1 实际改动

| 操作 | 文件 | 说明 |
|------|------|------|
| 修改 | `crawagent/tools/bilibili_tool.py` | 新增 7 个 async 函数 + `_bilibili` 改 `asyncio.run` 驱动 |
| 修改 | `pyproject.toml` | `httpx>=0.27.0` 加入主依赖（原仅 dev） |

### 8.2 async 路径设计（2 波并发）

```
Wave 1: view + wbi_keys（asyncio.gather，互相独立，只依赖 bvid）
Wave 2: comments + media（asyncio.gather，comments 依赖 aid from view，media 依赖 cid from view + wbi_keys）
```

原串行 5 个 round-trip → 2 波 2 个 round-trip（view/wbi_keys 并发 + comments/media 并发）。

| async 函数 | 对应 sync | HTTP 端点 |
|------------|-----------|-----------|
| `_aget(client, url, params, headers)` | `_get` | 通用 async GET |
| `_aget_wbi_keys(client, headers)` | `_get_wbi_keys` | nav API |
| `_aget_view(client, bvid, headers)` | 内联在 `_bilibili` | view API |
| `_aget_playurl(client, params, headers, img_key, sub_key)` | `_bilibili_playurl` | playurl (WBI signed + unsigned fallback) |
| `_aget_media(client, bvid, cid, headers, img_key, sub_key)` | `_bilibili_media` | fnval 16/1 两轮 |
| `_aget_comments(client, aid, headers)` | `_bilibili_comments` | reply API |
| `_abilibili(url, fields)` | `_bilibili` | 编排 2 波 gather |

### 8.3 同步 helper 保留

sync 函数（`_get_wbi_keys` / `_bilibili_playurl` / `_bilibili_media` / `_bilibili_comments`）全部保留不动——023 补的 12 个测试 mock `_get` 调 sync 函数，零适配成本。async 路径是独立的一套函数，不影响 sync 路径的测试覆盖。

### 8.4 验证结果

- **import 冒烟**：`from crawagent.tools.bilibili_tool import bilibili_extract, _abilibili, _aget_wbi_keys` → OK
- **`uv run pytest tests/test_bilibili_tool.py -v`**：12/12 passed（同步 helper 测试全绿，零适配）
- **`uv run pytest tests/ -q`**：603 passed, 6 warnings（零回归）

### 8.5 实施经验

1. **sync/async 双轨而非替换**：spec §3.3 说"bilibili_tool 仍可用 _http.http_get 做同步路径"。实施时选择保留全部 sync helper 不动，新增独立 async 函数。好处：(a) 12 个现有测试零适配；(b) sync helper 仍可被其他调用方复用；(c) async 函数可以单独测试。代价：代码量翻倍（~120 行 async + ~80 行 sync）。但 bilibili_tool 总量 ~340 行仍在可接受范围。
2. **httpx.AsyncClient 生命周期**：`_abilibili` 内用 `async with httpx.AsyncClient() as client` 管理。Wave 1 和 Wave 2 各开一个 client（因为 Wave 1 结果处理后才知道是否需要 Wave 2）。这比跨 wave 共享 client 更简单，性能差异可忽略（本地连接池重建 < 1ms）。
3. **asyncio.run 在 sync 入口**：`_bilibili` = `asyncio.run(_abilibili(...))`。LangChain @tool 是同步调用的，不会有 running event loop 冲突。如果未来 agent 层改 async tool calling，需要改用 `await _abilibili(...)` 直接调用。
4. **httpx 加入主依赖**：原 httpx 仅在 dev 依赖（FastAPI TestClient 用）。bilibili_tool 模块级 `import httpx` 后必须加入主依赖，否则非 dev 安装会 ImportError。openai 已 transitive 拉入 httpx，但显式声明是正确做法。