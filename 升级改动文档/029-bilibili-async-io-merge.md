# 变更 029：bilibili 6 端点 IO 合并（016 撤档后重新设计）

| 项目 | 内容 |
|------|------|
| 变更编号 | 029 |
| 提出日期 | 2026-09-20 |
| 状态 | 待实施（延后） |
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

**状态**：延后——async 重构工作量大，本批次优先完成 024-027 + 030

### 8.1 调研摘要

- `bilibili_tool.py` 的 `_bilibili` 函数仅 48 行（编排），实际 HTTP 调用分散在 6 个内部 helper（`_get_wbi_keys` / `_bilibili_view` / `_bilibili_playurl` / `_bilibili_media` / `_bilibili_comments`）
- 当前用 `requests.get`（同步），改 `httpx.AsyncClient` 需重写全部 6 个 helper + 编排函数
- 测试 mock 模式需从 `requests.get` 改为 `httpx.AsyncClient.get`（023 补的 bilibili 12 测试需适配）
- spec §3.1 的 3 波 gather 设计合理（keys → {view, playurl, comments} → media）

### 8.2 延后原因

1. **工作量大**：6 个 sync→async helper 改写 + 编排重写 + 12 测试 mock 模式适配 = ~200 行改动
2. **风险**：async/await 在 asyncio.run 包装层容易踩 event loop 嵌套坑
3. **优先级**：024/025/027/030 是结构性改进（完成后基础设施稳定），029 是性能优化（可延后）

### 8.3 后续实施建议

1. 先读 `_bilibili` 编排函数 + 6 个 helper 的当前签名
2. 逐个 helper 改 async（`_aget_wbi_keys` → `_aget_view` → ...）
3. 编排改 3 波 `asyncio.gather`
4. 同步入口 `bilibili_extract` 用 `asyncio.run(_abilibili(...))`
5. 测试 mock 改 `httpx.AsyncClient`（`monkeypatch` AsyncClient.get）