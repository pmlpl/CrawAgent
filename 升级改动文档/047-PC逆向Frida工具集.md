# 变更 047：PC 逆向 Frida 工具集（Windows 桌面应用加密接口破解）

| 项目 | 内容 |
|------|------|
| 变更编号 | 047 |
| 提出日期 | 2026-10-10 |
| 状态 | 已实施 |
| 类型 | 功能新增（工具与爬取能力） |
| 关联模块 | crawagent/tools/pc_reverse_tool.py（新建）、crawagent/tools/assets/pc_ssl_bypass.js（新建）、crawagent/prompts/system.md、tests/test_pc_reverse_tool.py（新建） |

---

## 〇、价值

- **改动前**：agent 面对 PC 桌面应用（QQ音乐PC版、Electron 应用、Windows 客户端等）的加密接口破解需求时，只有 Android 逆向工具（全绑 `-D device_id`，无法用于本地进程）和 anything-analyzer（网络层抓包，需要用户手动拉起）。实测给 agent 下达"找到目录→解包→搜索→读取"的明确指令后，agent 被迫用 `run_custom_script` 临场手搓 Python 脚本——LLM 每次拼出的 frida 命令可能错一个字符就失败，错误处理靠 traceback，输出无截断可能撑爆上下文，SSL bypass 脚本无法靠 LLM 从零写。
- **改动后**：agent 遇到 PC 目标时直接走专用工具链——`list_windows_processes` 列进程 → `frida_hook_pc_function` hook 加密函数 → `frida_dump_dll` dump DLL 模块 → `frida_bypass_pc_ssl` 绕过 Windows SSL 校验。命令参数预先写死不靠 LLM 拼，二进制三级查找复用 015 基建，输出截断复用现有 `_truncate`，SSL bypass 脚本预写覆盖 Windows 常见 SSL 栈。

## 一、背景与问题

1. 现有 `android_reverse_tool.py` 的 6 个工具全部绑 Android 设备：`frida_hook_function` 用 `frida-trace -D device_id -f package_name`，`frida_dump_so` 用 `frida -D device_id -f package_name`，`-D` 是远程设备参数，去掉才能 attach 本地 Windows 进程。
2. Java 检测逻辑（`"." in function_pattern` 判断是否 Java 方法）在 Windows 上会误判——Windows 函数名可能含点号（如 `kernel32.CryptEncrypt`），被误走 `-j` Java flag 导致 frida-trace 报错。
3. `ssl_pinning_bypass.js` 脚本 90% 是 Android-Java 专用（`Java.perform` 外壳 + OkHttp3/Conscrypt/TrustManagerImpl 等 Java 类 hook），在 Windows 进程上全段 `[SKIP]`。Windows 桌面应用常用 SChannel/WinHTTP（Windows 原生）、OpenSSL（内嵌）、Node.js TLS（Electron 应用），均不在覆盖范围内。
4. agent 无 `list_windows_processes` 工具，无法列出本地进程——相当于 Android 侧没有 `list_adb_devices` 一样的起步困境。
5. 实测（2026-10-10）agent 面对QQ音乐PC版破解任务时：第一轮纯咨询指令 → 0 工具调用，输出纯文字教程；第二轮明确执行指令 → 3 次 `run_custom_script` 手搓脚本，但本机未安装 QQ音乐导致扫盘失败后转向 GitHub 搜资料。两条路径都未触及真正的 hook/dump/bypass 能力。

## 二、目标

1. 新建 `crawagent/tools/pc_reverse_tool.py`，4 个 `@tool` 函数：
   - `list_windows_processes(pattern)` — 列本地 Windows 进程（按名过滤），返回 PID + 进程名 + 架构。
   - `frida_hook_pc_function(process_name, function_pattern, max_calls, timeout)` — frida-trace hook 本地 Windows 进程的函数。
   - `frida_dump_dll(process_name, dll_name, dump_offsets, timeout)` — dump 本地进程的 DLL 模块基址 + 导出偏移。
   - `frida_bypass_pc_ssl(process_name, timeout)` — 注入 Windows SSL bypass 脚本。
2. 新建 `crawagent/tools/assets/pc_ssl_bypass.js`，覆盖 Windows 常见 SSL 栈：OpenSSL/BoringSSL（`SSL_get_verify_result`）、SChannel（`SecPkgConnectionVerify`）、Node.js TLS（`tls.checkServerIdentity`）。
3. `system.md` 追加 4 个工具条目（编号 46-49）+ `PC 逆向 / Frida Hook Workflow` 段（与 Android Workflow 并列）。
4. 新建 `tests/test_pc_reverse_tool.py`，覆盖命令构造、二进制查找、错误提示、输出截断。
5. **不做**：macOS/Linux 进程列表（主环境 Windows，POSIX 的 `ps` 覆盖面够时二期再补）；前端设置页卡片（`frida_path` 已在 015 加过，PC 侧共用）；Windows 进程注入自动化（spawn + attach 的 `-f` 模式留给二期，一期只做 attach 运行中进程的 `-n` 模式）。

## 三、方案设计

### 3.1 工具复用与差异

| 辅助函数 | 来源 | PC 侧改动 |
|---------|------|-----------|
| `_check_binary` | 015 已有 | 直接复用，frida/frida-trace 共用 `frida_path`，无新二进制 |
| `_run_frida` | android_reverse_tool 已有 | 直接复用（subprocess 调 frida CLI，跨平台） |
| `_truncate` | android_reverse_tool 已有 | 直接复用 |
| `_rel_path` | android_reverse_tool 已有 | 直接复用 |
| `_run_adb` | android_reverse_tool 已有 | **不用**（PC 无 adb） |
| `_common_location_binary` | 015 已有 | **不用**（PC 无 adb 位置表，frida 靠 pip + which） |

**设计决策**：辅助函数从 `android_reverse_tool` import 复用，不复制粘贴。`pc_reverse_tool.py` 只写 4 个 `@tool` 函数 + PC 专用辅助（进程列表、PC SSL 脚本路径）。这要求 `android_reverse_tool.py` 的辅助函数是模块级公开的（当前已经是，加 `__all__` 或直接 import 即可）。

### 3.2 工具 1：list_windows_processes

```python
@tool
def list_windows_processes(pattern: str = "", timeout: int = 10) -> str:
    """列出本地 Windows 进程（可选按名过滤）。"""
```

- **实现**：`subprocess.run(["tasklist", "/Fo", "CSV", "/Nh"], ...)` → 解析 CSV → 按名过滤。
- **无 tasklist 时的兜底**：用 `psutil`（已在依赖树里的话），否则返回 ERR。
- **输出格式**：`pid=1234 | name=QQMusic.exe | arch=64bit`（每行一台）。
- **pattern**：不区分大小写的子串匹配（空 = 全部，`QQMusic` = 只返回含该串的）。
- **FRIDA 进程过滤**：返回结果里排除 `frida` 自身进程（避免 agent hook 自己）。

### 3.3 工具 2：frida_hook_pc_function

```python
@tool
def frida_hook_pc_function(process_name: str, function_pattern: str,
                           max_calls: int = 20, timeout: int = 30) -> str:
    """用 frida-trace hook 本地 Windows 进程的函数。"""
```

- **命令构造**：
  ```
  frida-trace -n <process_name> --no-pause -i <function_pattern>
  ```
- **与 Android 版差异**：
  - 去掉 `-D device_id`（本地进程不需要远程设备参数）。
  - 去掉 `-f package_name`（一期不做 spawn，只 attach 运行中进程；`-n` 按名 attach）。
  - **去掉 Java 检测逻辑**（`"." in function_pattern` 判断 Java）——PC 上不走 `-j` Java flag，统一走 `-i` native 符号 glob。Java 检测是 Android 专用，PC 进程没有 Java VM。
- **function_pattern**：native 符号 glob（如 `*CryptEncrypt*`、`*sign*`、`*AES*`）。
- **max_calls**：通过 frida-trace 的 `--max-events` 限制（如不支持则靠 timeout 截断）。

### 3.4 工具 3：frida_dump_dll

```python
@tool
def frida_dump_dll(process_name: str, dll_name: str,
                   dump_offsets: bool = True, timeout: int = 30) -> str:
    """附加本地进程，dump 指定 DLL 的基址 + 导出函数偏移。"""
```

- **命令构造**：
  ```
  frida -n <process_name> --no-pause -e <inline_js>
  ```
- **JS 模板**：复用 `_DUMP_SO_JS_TEMPLATE`（`Process.findModuleByName` 对 DLL 同样有效），`__SO_NAME__` 占位符传入 dll_name。
- **与 Android 版差异**：
  - 去掉 `-D device_id -f package_name`，改 `-n process_name`。
  - 参数语义 `so_name` → `dll_name`（不影响 JS 执行，只是文档/参数名层面）。
  - 导出枚举的常见符号过滤从 `Java_*/SSL_*/AES_*` 调整为 `Crypt*/SSL*/sign*/encrypt*`（Windows 导出命名习惯）。

### 3.5 工具 4：frida_bypass_pc_ssl

```python
@tool
def frida_bypass_pc_ssl(process_name: str, timeout: int = 30) -> str:
    """注入 Windows SSL bypass 脚本，绕过本地进程的证书校验。"""
```

- **命令构造**：
  ```
  frida -n <process_name> --no-pause -l <pc_ssl_bypass.js>
  ```
- **pc_ssl_bypass.js 覆盖范围**：

| 段 | SSL 栈 | hook 点 | 适用场景 |
|----|--------|---------|---------|
| 1 | OpenSSL / BoringSSL | `SSL_get_verify_result` → 返回 0 | 内嵌 OpenSSL 的 C/C++ 应用 |
| 2 | SChannel（Windows 原生） | `SecPkgConnectionVerify` / `SslVerifyCertificate` | 使用 WinHTTP/WinINet 的 Windows 原生应用 |
| 3 | Node.js TLS（Electron） | `tls.checkServerIdentity` → 返回 undefined | Electron 应用（QQ音乐PC版等） |
| 4 | CryptoAPI 证书校验 | `CertVerifyCertificateChainPolicy` → 返回 TRUE | 使用 CryptoAPI 的传统 Windows 应用 |

- **与 Android ssl_pinning_bypass.js 差异**：
  - 去掉 `Java.perform(...)` 外壳（无 Java VM）。
  - 去掉 OkHttp3/Conscrypt/TrustManagerImpl/HostnameVerifier 段（Java 类不存在）。
  - 保留并增强 native SSL 段（`SSL_get_verify_result`），新增 SChannel/Node.js/CryptoAPI 段。
  - 输出标记沿用 `[BYPASS]` / `[SKIP]` 格式（上层工具的统计逻辑不变）。

### 3.6 system.md 契约追加

**工具清单段**（在 45. fetch_rss_feed 之后追加 46-49）：

```
46. list_windows_processes(pattern) — List local Windows processes (optional name filter). Returns PID + name + arch. ALWAYS call this FIRST before any PC Frida tool — confirms the target process is running. Missing process / no match all return a specific status string; do NOT pre-judge — call it.
47. frida_hook_pc_function(process_name, function_pattern) — Hook native functions on a local Windows process via `frida-trace -n`. function_pattern is a native symbol glob (`*CryptEncrypt*`, `*sign*`). Outputs trace log showing args/return values. Use to locate the encryption function the desktop App calls.
48. frida_dump_dll(process_name, dll_name) — Dump a DLL module's base address + export offsets via inline Frida JS. dll_name is the base name (e.g. `ncrypt.dll`, `libcrypto.dll`). Use with IDA/Ghidra for offline analysis.
49. frida_bypass_pc_ssl(process_name) — Attach the built-in Windows SSL bypass script. Covers OpenSSL / SChannel / Node.js TLS / CryptoAPI. Use BEFORE the MCP Capture Workflow — bypass first, then capture plaintext traffic.
```

**Workflow 段**（在 Android Workflow 之后并列追加）：

```
PC 逆向 / Frida Hook Workflow (处理 Windows 桌面应用加密函数定位、DLL dump、SSL pinning 绕过):
Step 0 — 进程就位 (MUST): 调 list_windows_processes(pattern="QQMusic") 确认目标进程在运行。HARD: 不许凭"反正要 frida"预判拒绝、不许跳过调用直接写教程——工具会返回精确状态串（"ERR: tasklist 未安装..." / "NO_PROCESS: ..." / "找到 N 个进程"），你照原样上报 + ask_user 问下一步（启动目标程序 / 安装 frida）。拿到 NO_PROCESS → 告诉用户先启动目标应用再继续。
Step 1 — 定位加密函数: frida_hook_pc_function(process_name, "*sign*") — 挂载 frida-trace，操作目标应用触发加密调用时输出函数 args/return。native 符号用 glob 格式（`*CryptEncrypt*` / `*sign*` / `*AES*`）。
Step 2 — 绕过证书锁定: frida_bypass_pc_ssl(process_name) — 挂载内置 Windows SSL bypass 脚本。挂上后立刻走上面的 MCP Capture Workflow 抓明文流量。
Step 3 — dump DLL: frida_dump_dll(process_name, "libcrypto.dll") — dump 指定 DLL 的内存基址 + 导出偏移，配合 IDA/Ghidra 离线分析。
- frida/frida-trace 必须装在主机上（pip install frida-tools）。与 Android 版共用 frida_path 配置（015）。
- frida_hook_pc_function 的 trace 日志可能很大——按 function_pattern 精确定位，别用太宽的 glob。
- SSL bypass 与 MCP Capture Workflow 是串联关系：先 bypass，再 capture。
- 一期只支持 attach 运行中进程（-n 模式）；spawn + attach（-f 模式）留给二期。
```

### 3.7 工具数与测试计数同步

- system.md 工具清单总数从 45 → 49（+4）。
- wiki/03-工具与技能总览.md 工具数同步更新。
- AGENTS.md 基线测试计数：pytest 从 329 → 333（+4 类 × 各自用例，实际以测试文件为准）。
- vitest 不受影响（纯后端工具）。

## 四、涉及文件清单

| 操作 | 文件 | 说明 |
|------|------|------|
| 新建 | crawagent/tools/pc_reverse_tool.py | 4 个 @tool 函数 + 进程列表辅助 + PC SSL 脚本路径辅助；从 android_reverse_tool import `_check_binary`/`_run_frida`/`_truncate`/`_rel_path` |
| 新建 | crawagent/tools/assets/pc_ssl_bypass.js | Windows SSL bypass 脚本（4 段：OpenSSL/SChannel/Node.js TLS/CryptoAPI） |
| 修改 | crawagent/prompts/system.md | 追加工具 46-49 条目 + PC 逆向 Workflow 段；工具总数 45→49 |
| 新建 | tests/test_pc_reverse_tool.py | list_windows_processes 解析、frida 命令构造（-n 无 -D）、dump DLL JS 模板、SSL bypass 脚本存在性、错误提示串 |
| 修改 | crawagent/tools/android_reverse_tool.py | 辅助函数加 `__all__` 导出声明（可选，确保 pc_reverse_tool 能 import） |
| 修改 | wiki/03-工具与技能总览.md | 工具数 +4，追加 PC 逆向工具段 |
| 修改 | AGENTS.md | 基线 pytest 计数同步 |

## 五、验证方式

1. **进程列表解析**：mock `subprocess.run` 返回假 tasklist CSV → `list_windows_processes` 正确解析 PID/name/arch；pattern 过滤命中；空结果返回 `NO_PROCESS`。
2. **frida 命令构造**：mock `_check_binary` 返回假路径 → `frida_hook_pc_function` 构造的命令含 `-n process_name --no-pause -i pattern`，**不含** `-D`；mock `_run_frida` 返回假 trace 日志 → 输出含 header + 截断后内容。
3. **dump DLL JS**：`frida_dump_dll` 生成的内联 JS 含 `Process.findModuleByName('test.dll')` + `enumerateExports`；dump_offsets=False 时含跳过标记。
4. **SSL bypass 脚本存在性**：`pc_ssl_bypass.js` 文件存在且内容含 `SSL_get_verify_result` + `SecPkgConnectionVerify` / `tls.checkServerIdentity` + `CertVerifyCertificateChainPolicy` 段标记。
5. **错误提示**：frida 未安装 → 返回串含 `pip install frida-tools`；进程不存在 → mock tasklist 返回空 → `NO_PROCESS`。
6. **输出截断**：mock `_run_frida` 返回 10000 字符 → `_truncate` 限 4000 + 截断标记。
7. **system.md 一致性**：工具条目数 = 49；PC Workflow 段存在且含 Step 0-3。

## 六、实施记录（人话版，2026-10-10）

**落地文件**：
- 新建 `crawagent/tools/pc_reverse_tool.py`（4 个 `@tool`：`list_windows_processes` / `frida_hook_pc_function` / `frida_dump_dll` / `frida_bypass_pc_ssl`）。辅助函数（`_check_binary`/`_run_frida`/`_truncate`/`_rel_path`/`_DUMP_SO_JS_TEMPLATE`）从 `android_reverse_tool` import 复用，不复制粘贴；`list_windows_processes` 调 `tasklist /Fo CSV /Nh` 解析，排除 frida 自身进程；PC 命令统一 `-n process_name`，不含 `-D`/`-f`，不走 Java 检测（PC 无 JVM）。
- 新建 `crawagent/tools/assets/pc_ssl_bypass.js`（4 段：OpenSSL/BoringSSL `SSL_get_verify_result`+`SSL_verify_cert_chain`、SChannel `SecPkgConnectionVerify`/`SslVerifyCertificate`、Node.js TLS `tls.checkServerIdentity`、CryptoAPI `CertVerifyCertificateChainPolicy`+`WinVerifyTrust`）。无 `Java.perform` 外壳。
- `android_reverse_tool.py` 末尾加 `__all__` 导出契约，明确 pc_reverse_tool 可 import 的公开符号。
- `registry.py` `_TOOL_META` 补 4 个 PC 工具归 `pc_reverse` 类别（避免被 `_guess_category` 误判到 android/extract）；`cat_labels` 加 `🖥️ PC Reverse` 标签。
- `system.md` 追加工具 46-49 条目 + PC 逆向 Workflow 段（Step 0-3，与 Android 并列）；工具总数 45→49。

**测试**：新建 `tests/test_pc_reverse_tool.py`（24 用例：CSV 解析、pattern 过滤、NO_PROCESS、frida 自身排除、非 Windows 兜底、tasklist 缺失、命令无 `-D`/`-f`、Java 检测不走、输出截断、JS 模板替换、dump_offsets 跳过、DUMP_FAIL、脚本存在性、4 段标记、BYPASS_OK/FAIL、system.md 49 条 + PC Workflow Step 0-3）。全绿。

**契约同步**：
- `test_phase2_registry.py` 内置工具计数 44→48（+4）。
- `000-目录.md` 登记 047 行 + 快照 46→47 份（已实施 44→45）。
- 规格 meta 状态「待批准 → 已实施」。

**未做**（规格第二节已声明边界）：macOS/Linux `ps` 进程列表、spawn+attach `-f` 模式、前端设置页卡片（`frida_path` 015 已加，PC 侧共用）。
