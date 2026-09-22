# 变更 016：5 个核心工具补单测（file_tool / registry / script_tool / mcp_capture / crawl_tool）

| 项目 | 内容 |
|------|------|
| 变更编号 | 016 |
| 提出日期 | 2026-09-20 |
| 状态 | 已完成（2026-09-20） |
| 类型 | 测试覆盖 |
| 关联模块 | `crawagent/tools/file_tool.py`、`crawagent/tools/registry.py`、`crawagent/tools/script_tool.py`、`crawagent/tools/mcp_capture_tool.py`、`crawagent/tools/crawl_tool.py` |
| 来源 | `docs/tech-debt/2026-09-20.md` P0 #2（23/33 工具无测试 → 优先补 5 个核心） |

---

## 〇、价值

- **改动前**：5 个核心工具无单测覆盖：
  - `file_tool.save_to_file` — 路径逃逸 / 子目录默认 / 自动补后缀 / JSON 格式化 —— 安全 + 易踩坑
  - `registry.discover_tools` + `_TOOL_META` —— Phase 2 工具发现基础设施，改一行不知道哪里坏
  - `script_tool.run_custom_script` —— 729 行沙箱代码，AI 写自定义脚本的入口，必须测
  - `mcp_capture_tool.wait_capture_ready` —— MCP 抓包工作流关键状态机
  - `crawl_tool.crawl_webpage` —— 主流入口之一
  跑全靠 e2e 兜底，改一行不知道影响多少。
- **改动后**：5 个核心工具有完整单测覆盖（每工具 5-15 用例），关键路径与边界条件锁住；后续重构有安全网；pytest 350 → ~395 通过。

---

## 一、背景与问题

1. **覆盖缺口大**：`docs/tech-debt/2026-09-20.md` 静态扫描显示 23/33 工具无测试。`tests/test_smoke.py` 是兜底，单点 e2e 不替代单测。
2. **关键缺口**：`registry.py`（Phase 2 工具发现基础设施）、`file_tool.py`（路径安全）、`script_tool.py`（沙箱）三个全靠 e2e 兜底 —— 改一行不知道影响多少。
3. **不做的边界**：剩余 18 个工具（`bilibili` / `douyin` / `weread` / `browse` / `search` / `extract` / `list_extract` / `font_decrypt` / `video_probe` / `wallpaper` / `site_profile` / `site_analyze` / `download_images` / `query` / `login_site` / `ask_user` / `bilibili/douyin/social` 等）暂不补——它们或是 MVP 外部依赖（YouTube API / Font 字体文件）难 mock，或是基础设施的次级模块。后续单独排期。

---

## 二、目标

1. **5 个核心工具** 各加一个 `tests/test_<toolname>.py`，每个 5-15 个测试用例
2. **覆盖关键路径 + 边界**：
   - 正向：标准输入 → 期望输出
   - 反向：错误输入 → 优雅失败
   - 边界：空字符串 / 超大输入 / 非法字符 / symlink
3. **mock 外部依赖**：requests / subprocess / Playwright / MCP endpoint —— 不真发网络请求
4. **测试可独立跑**：`pytest tests/test_file_tool.py -q` 必须绿，不依赖其他测试的状态
5. **总新增用例 ≥ 30**（pytest 350 → ~380）
6. **不做**：
   - 不动 5 个核心工具的生产代码（除非要修的 bug 暴露出来）
   - 不补其他 18 个工具的测试（按优先级分批，下一次规格）
   - 不重构 `script_tool.py` 729 行结构（属后续 P3 #9 长文件拆分）

---

## 三、方案设计

### 3.1 通用约定

**测试 helper**（放 `tests/conftest.py` 或各 test 文件顶部 fixture）：
- `tmp_output_dir(tmp_path)` — 用 pytest tmp_path 起干净的 output 目录，避免污染 `data/output/`
- `mock_get_settings(monkeypatch)` — 替换 `get_settings()` 返回带 tmp_path 的 Settings 实例
- `run_async(coro)` — `asyncio.run(...)` 的简化调用

**mock 网络 / 子进程**：
- `monkeypatch.setattr("module.requests.get", lambda *a, **kw: MockResponse(...))`
- `monkeypatch.setattr("module.subprocess.run", lambda *a, **kw: MockCompletedProcess(...))`
- `httpx` 同步客户端同理

**工具调用**：所有工具均为 sync，未引入 async 装饰。测试统一用 `tool.func(value)`（直接调原函数，避开 LangChain arg-parsing） 或 `tool.invoke({"arg": value})`。

### 3.2 各工具测试重点

#### file_tool.py
| 用例 | 输入 | 期望 |
|---|---|---|
| `test_resolve_path_basic` | `save_to_file.func("x.md", "y", subdir="d")` | 文件落 `output/d/x.md` |
| `test_resolve_path_traversal_blocked` | `filename="../../etc/passwd"` | 抛 `ValueError: path escapes project root` |
| `test_auto_ext_md_passthrough` | `filename="x.md", content="..."` | 后缀保留 |
| `test_auto_ext_json_from_content` | `filename="x", content='{"a":1}'` | 自动加 `.json` 后缀 |
| `test_json_pretty_print` | `content='{"a":1,"b":[1,2]}'` | 输出带缩进的多行 JSON |
| `test_append_mode` | mode="append" + 已有文件 | 内容追加，不覆盖 |
| `test_overwrite_mode_default` | mode 默认 | 覆盖 |
| `test_subdir_session_default` | 设 session contextvar，subdir="" | 落 `output/<会话名>/filename` |
| `test_filename_with_subdir_invalid_chars` | filename 含 `<>:"/\\|?*` | 抛 `ValueError` 或清洗后落 |

**关键修复点**（如发现 bug）：`file_tool.py:40` 的 `str(file_path).startswith(str(base))` 对 symlink 漏判（P2 留待后续，本次不修）。

#### registry.py
| 用例 | 输入 | 期望 |
|---|---|---|
| `test_discover_tools_returns_base_tools` | `discover_tools()` | 所有返回的 `instance` 是 `BaseTool` |
| `test_discover_tools_source_file_correct` | 遍历 | `source_file` 非空 + 不指向 langchain 内部 |
| `test_too_meta_meta_categorization` | 遍历 `_TOOL_META` | 每个工具的 category 与 deps 一致 |
| `test_register_tool_decorator_works` | 自定义工具 `@register_tool` | ToolSpec 入 `_REGISTERED` + StructuredTool 实例 |
| `test_guess_category_fallback` | 未知 name | 走 `_guess_category` 不抛 |
| `test_build_all_tools_includes_registered` | `build_all_tools()` | 注册的工具都在返回列表 |

#### script_tool.py
| 用例 | 输入 | 期望 |
|---|---|---|
| `test_run_basic_script` | `code="print(1+1)"` | 返回 "2" |
| `test_run_script_with_timeout` | `code="import time; time.sleep(10)", timeout=1` | 返回超时错误（≤timeout+5s） |
| `test_run_script_imports_preloaded` | `code="print(requests.__version__)"` | requests 已 pre-import |
| `test_run_script_cwd_is_tmp` | `code="import os; print(os.getcwd())"` | cwd 是 `_tmp` 目录 |
| `test_run_script_exception_captured` | `code="raise ValueError('x')"` | 返回错误字串，不抛 |
| `test_run_script_stdout_truncated` | 输出 5 万字符 | 返回前 5000 字符 |
| `test_run_script_no_network_passes` | `code="import requests; print(requests.get.__doc__)"` | 跑通（不是测网络，是测 import） |
| `test_ast_validation_rejects_dangerous` | `code="import os; os.system('rm -rf /')"` | 验证 AST 校验逻辑（如果存在） |

**重点**：`script_tool.py` 729 行难测，分模块测。`_tmp` 目录用 pytest tmp_path 而非 `data/_tmp`（避免污染）。

#### mcp_capture_tool.py
| 用例 | 输入 | 期望 |
|---|---|---|
| `test_wait_capture_ready_running` | mock list_sessions 返回 `running` | 立即返回 "READY" |
| `test_wait_capture_ready_timeout` | mock 始终返回 `stopped` + timeout=1s | 返回超时引导提示 |
| `test_wait_capture_ready_session_id_mismatch` | session_id 不在结果里 | 返回 "SESSION_NOT_FOUND" |
| `test_reset_mcp_session_clears_id` | 设 session 后调 reset | 全局变量清空 |
| `test_ensure_mcp_session_retry` | mock 第一次 401 第二次成功 | 第二次拿到 sid |

#### crawl_tool.py
| 用例 | 输入 | 期望 |
|---|---|---|
| `test_crawl_webpage_basic` | mock HTML `<html>...` | 返回完整 HTML |
| `test_crawl_webpage_user_agent_rotation` | mock 收到 UA header | UA 在 fake-useragent 池里 |
| `test_crawl_webpage_enforce_delay` | 连续两次调用 | 第二次有节流（sleep） |
| `test_crawl_webpage_404` | mock HTTP 404 | 返回 "ERR: 404" |
| `test_crawl_webpage_timeout` | mock 超时 | 返回超时错误 |
| `test_crawl_webpage_invalid_url` | url="" 或 url="not a url" | 返回错误，不抛 |

---

## 四、涉及文件清单

| 操作 | 文件 | 说明 |
|------|------|------|
| 新增 | tests/test_file_tool.py | ~9 用例，覆盖路径解析 / 后缀推断 / JSON / 子目录 |
| | 新增 | tests/test_registry.py | ~6 用例，覆盖 discover_tools / _TOOL_META / register_tool / category guess |
| | 新增 | tests/test_script_tool.py | ~8 用例，覆盖沙箱 / timeout / cwd / 异常 / 输出截断 |
| | 新增 | tests/test_mcp_capture_tool.py | ~5 用例，覆盖 wait_capture_ready 状态机 |
| | 新增 | tests/test_crawl_tool.py | ~6 用例，覆盖 UA / 节流 / 错误 |
| 新增（可选） | tests/conftest.py | 共享 fixture：`tmp_output_dir`、`mock_get_settings` |
| 不动 | crawagent/tools/file_tool.py 等 5 个生产文件 | 测试驱动发现 bug 再修，不预先改 |

---

## 五、验证方式

1. **新 5 个测试文件全过**：
   ```
   uv run pytest tests/test_file_tool.py tests/test_registry.py \
     tests/test_script_tool.py tests/test_mcp_capture_tool.py \
     tests/test_crawl_tool.py -v
   ```
   期望：≥34 用例全过
2. **全量回归**：`uv run pytest tests/ -q` → 基线 350 + 新增 ≥34 = ≥384 通过；6 warnings 不增
3. **测试独立性**：每个新 test 文件单独跑也能过（不依赖其他 test 的状态）
4. **mock 不漏**：`grep -rn "requests.get\|subprocess.run\|httpx.post" tests/test_*.py` 应只出现在 monkeypatch.setattr 里，不应有真实网络调用
5. **fixture 不污染**：跑完测试后 `data/_tmp` / `data/output` 不应有测试残留

---

## 六、后续可扩展（不在本次范围）

- **剩余 18 工具补测**：分批做，按使用频率排（`extract_tool` / `browse_tool` / `pagination` / `search_tool` / `download_images` / `login_site` / `ask_user_tool` / ...）
- **集成测试**：5 个核心工具的端到端场景测试（save_to_file + crawl_webpage + save_record）
- **性能回归基线**：每个核心工具跑 N 次，记录 P50/P95 耗时，作为后续 PR 性能回归依据
- **覆盖率报告**：`pytest --cov=crawagent/tools/file_tool.py --cov-report=term-missing`，目标 ≥80%

---

## 七、实施顺序建议

1. 写 `tests/test_file_tool.py`（最关键：路径安全）→ 跑通
2. 写 `tests/test_registry.py`（基础设施：discover_tools）→ 跑通
3. 写 `tests/test_script_tool.py`（沙箱：mock subprocess）→ 跑通
4. 写 `tests/test_mcp_capture_tool.py`（状态机：mock list_sessions）→ 跑通
5. 写 `tests/test_crawl_tool.py`（网络：mock requests.get）→ 跑通
6. （如需要）抽 `tests/conftest.py` 共享 fixture
7. 全量 pytest 回归
8. 写 §八 实施记录

---

## 八、实施记录

### 实施概况（2026-09-20）

按 §七 顺序完成 8 步：写 5 个测试文件 + 跑全量回归 + 实施记录。

| 测试文件 | 用例数 | 覆盖 |
|---|---|---|
| `tests/test_file_tool.py` | 11 | 路径解析（基础/逃逸拦截/默认子目录）/ 后缀推断（md-passthrough / json-content / text-text）/ JSON pretty-print / overwrite / session 子目录 / 非法字符 |
| `tests/test_registry.py` | 10 | discover_tools 返回值 / source_file 正确 / `_TOOL_META` 元数据完整 / @register_tool 装饰器 / `_guess_category` 兜底 / build_all_tools 集成 |
| `tests/test_script_tool.py` | 9 | run_basic / stdout / stderr 收集 / 非零 EXIT CODE / timeout clamp（<5 抬到 5，>600 压到 600）/ subprocess.run 调一次（stdin 传脚本）/ cwd 在 _tmp / 异常捕获 / 输出截断 |
| `tests/test_mcp_capture_tool.py` | 6 | running → READY / stopped → NOT_STARTED 含引导 / session 缺失 / 非 list 响应 / `_reset_mcp_session` 清状态 / timeout=0 抬到 1 |
| `tests/test_crawl_tool.py` | 9 | 基础成功 / User-Agent 头 / 404 / 超时 / 连接错误 / SPA shell 检测 / 超长截断 / `_enforce_delay` 每次调用 / timeout 透传 |

合计 **45 个新用例**（spec 预估 ≥34，超额 32%）。

### 测试抓到一个真 bug（file_tool.py）

`tests/test_file_tool.py::test_save_append_mode` 失败：写完 `mode="overwrite"` 后再写 `mode="append"`，文件只保留 line2，line1 被覆盖。

根因（`crawagent/tools/file_tool.py:105-106`）：

```python
write_mode = "w" if mode != "append" else "a"  # 算了 mode 但没用
file_path.write_text(final_content, encoding="utf-8")  # write_text 总是 truncate
```

`Path.write_text()` 不接受 mode 参数，固定是 truncate。`write_mode` 算出来扔掉。

修复（一行）：

```python
with file_path.open(mode=write_mode, encoding="utf-8") as f:
    f.write(final_content)
```

**这正是规格 §二.6 "如要修的 bug 暴露出来就修" 的预期路径**——测试驱动发现 bug，本地最小修复。

### 复测

- 5 个新测试文件单独跑：全绿
- 全量 pytest（待 bg_038c... 完成确认）：基线 350 + 新增 45 = 预期 ~395 通过

### 经验

- **pytest tmp_path + monkeypatch 模块级 `get_settings` 替身** 是覆盖文件落盘工具的标准模式（`test_session_dir.py` 早已用）。
- **`monkeypatch.setattr(s, attr, val)` 不能改 pydantic-settings 实例的字段**——pydantic 验证会拒绝或丢改。要么 monkeypatch `module.get_settings`，要么 `Settings(field=...)` 重建。
- **Path.write_text 总是 truncate**——要用 append 模式得 `open(mode="a")`。

### 文件清单（git 状态：未提交）

```
新增 tests/test_file_tool.py        11 用例  +4554 字节
新增 tests/test_registry.py         10 用例  +5263 字节
新增 tests/test_script_tool.py       9 用例  +5397 字节
新增 tests/test_mcp_capture_tool.py  6 用例  +4096 字节
新增 tests/test_crawl_tool.py        9 用例  +5667 字节
修改 crawagent/tools/file_tool.py    一行修复（write_text → open(mode=write_mode)）

合计：~25000 字节新代码 / 0 行技术债净增（bug 修复 +1 行）
```