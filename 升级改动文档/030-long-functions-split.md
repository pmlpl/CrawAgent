# 变更 030：长函数拆解（候选清单）

| 项目 | 内容 |
|------|------|
| 变更编号 | 030 |
| 提出日期 | 2026-09-20 |
| 状态 | 已实施（部分） |
| 类型 | 结构重构 |
| 关联模块 | 多个长函数 |
| 来源 | `docs/tech-debt/2026-09-20.md` P4 #14 |

---

## 〇、价值

- **改动前**：`scripts/_audit_long_functions.py` 静态扫描发现 50+ public 函数 > 40 行。最大的是：
  - `browse_tool.py:82 browse_and_crawl` 187 行
  - `graph/subagents/video_finder.py:175 run_video_finder` 141 行
  - `graph/skills.py:249 ensure_mcp_started` 140 行
  - `graph/middleware.py:267 wrap_model_call` 132 行
  - `tools/script_tool.py:758 run_custom_script` 122 行（027 拆文件后可能更短）
  - `tools/social_tool.py:75 download_social_media` 114 行
- **改动后**：选 2-3 个最大 / 最频繁撞到的拆解（按文件改一处开屏原则）

---

## 一、背景与问题

spec 原 P4 #14 写的 `_resolve_file_path` 已 019 改过（现在 25 行，< 40 行阈值）。实际候选清单更长，需要按影响力选 2-3 个拆。

候选（> 80 行 + 高频撞到）：
1. `browse_and_crawl`（187 行）— 主流入口之一，Playwright 浏览 + 渲染 + 提取全堆一起
2. `run_custom_script`（122 行）— 027 拆文件后剩主函数，仍过长
3. `wrap_model_call`（132 行）— middleware 核心，每次 LLM 调用都过
4. `ensure_mcp_started`（140 行）— MCP 启动逻辑多分支

---

## 二、目标

选 2 个最大 / 最高频的函数拆解：

### 2.1 候选 A：`browse_and_crawl`（187 行 → 拆 4-5 个 helper）

| 新函数 | 职责 |
|--------|------|
| `_open_browser(url)` | Playwright 启动 + 跳转 |
| `_wait_for_render(page)` | 等待 SPA 渲染 |
| `_extract_main_content(page)` | DOM 提取正文 |
| `_capture_network_logs(page)` | 网络日志 + 媒体收集 |
| `browse_and_crawl` | 编排 4 个 helper |

### 2.2 候选 B：`run_custom_script`（122 行 → 拆 3 个 helper）

| 新函数 | 职责 |
|--------|------|
| `_prepare_venv(script_path)` | 创建/复用 venv |
| `_run_subprocess(venv_python, code)` | 执行 + 输出收集 |
| `_cleanup_output(stdout, stderr)` | 脱敏 + 截断 |
| `run_custom_script` | 编排 3 个 helper |

### 2.3 备选：先选 2 个，按实施进度决定是否做第 3 个

---

## 三、方案设计

### 3.1 拆解原则

- **行为不变**：纯结构调整，外部签名 + 行为完全等价
- **私有 helper**：拆出的新函数加 `_` 前缀，不暴露为 @tool
- **每步跑 pytest**：每个函数拆完跑全量验证
- **测试覆盖**：023 补的 browse_tool.py / script_tool.py 测试必须保护所有分支

### 3.2 不动

- 函数对外签名（@tool 装饰的 `.func()` 不变）
- 公共 API（langchain 看到的工具描述不变）
- AST 校验逻辑（已有 `_ast_check` helper）
- 网络层（仍用 `_http.http_get` 或 `requests.get`）

---

## 四、涉及文件清单

| 操作 | 文件 |
|------|------|
| 修改 | `crawagent/tools/browse_tool.py`（拆 `browse_and_crawl`） |
| 修改 | `crawagent/tools/script_tool.py`（拆 `run_custom_script`，027 拆文件后） |
| 可能新增 helper | 同文件内 `_` 前缀函数 |

---

## 五、验证方式

1. **全量 pytest**：410+ passed 不变（023 补的测试保护）
2. **手动冒烟**：
   - `browse_and_crawl` 跑 1 个 SPA 站点
   - `run_custom_script` 跑 1 个示例脚本

---

## 六、后续可扩展（不在本次范围）

- 剩余 47 个 > 40 行函数（按需拆）
- 长函数与设计债分离（不是所有长函数都该拆；有些是领域复杂度）

---

## 七、实施顺序建议

1. 先 023 测试到位（保护所有分支）
2. 拆 `browse_and_crawl`（最大、最主流）
3. 拆 `run_custom_script`（027 拆文件后顺手）
4. （可选）拆 `wrap_model_call`（如果上两步顺利）

---

## 八、实施记录

**实施日期**：2026-09-22
**实施人**：Agent（指挥官 Joker 批准）

### 8.1 browse_and_crawl 拆分（已完成）

原函数 187 行 → 111 行 + 3 个模块级 helper：

| 新函数 | 行 | 职责 |
|--------|----|------|
| `_build_proxy_kwargs()` | 24 | Playwright launch 参数构建（代理透传） |
| `_format_chapter_list(result, task)` | 10 | 章节列表页格式化 |
| `_process_reader_page(result, url, task)` | 41 | 阅读器页处理（字体解密 + VIP 锁定代理 + HTML→MD） |
| `browse_and_crawl`（编排） | 111 | docstring 57 行 + JS evaluate 76 行（字符串字面量） + 编排 8 行 |

`browse_and_crawl` 剩余 111 行中，76 行是 `page.evaluate()` 的 JS 字符串字面量（不可执行逻辑，不可再拆）。实际可执行代码 ~35 行。

### 8.2 run_custom_script 评估（不拆）

`run_custom_script` 123 行，但 57 行是 @tool docstring（Agent 行为契约，不可缩），实际代码 65 行。代码结构清晰（组装 header → subprocess → 格式化输出），拆成 3 个 helper 会增加参数传递复杂度而不增加可读性。spec §2.2 提议的 `_prepare_venv` / `_run_subprocess` / `_cleanup_output` 与实际代码不匹配（实际无 venv 创建，用 `sys.executable` 直接跑）。

**决定**：不拆 `run_custom_script`，实际代码 65 行在可接受范围。

### 8.3 验证结果

- **`uv run pytest tests/ -q`**：603 passed, 6 warnings（零回归）
- **`tests/test_browse_tool.py`**：12/12 passed（monkeypatch `_build_proxy_kwargs` + Playwright mock 保护所有分支）

### 8.4 实施经验

1. **JS 字符串不可拆**：`page.evaluate()` 的 76 行 JS 是一个字符串字面量，不是可执行 Python 逻辑。拆它到模块级常量可以，但不减少函数"复杂度"——只是移位置。选择留在原位。
2. **docstring 占比**：`run_custom_script` 123 行中 57 行（46%）是 docstring。评估长函数时应区分 docstring 和代码——65 行代码不需要拆。
3. **Edit 逐步拆 vs 脚本一次性拆**：`browse_and_crawl` 用 Edit 逐步拆（插入 helper → 替换内联代码）比脚本一次性提取更可控。脚本方法在 JS 字符串边界处理上容易出错。