# 变更 015：Android 工具链二进制自动定位（PATH 外常见位置 + ADB_PATH/FRIDA_PATH）

| 项目 | 内容 |
|------|------|
| 变更编号 | 015 |
| 提出日期 | 2026-09-19 |
| 状态 | 已完成（2026-09-19） |
| 类型 | bug 修复（工具可用性） |
| 关联模块 | crawagent/tools/android_reverse_tool.py、crawagent/config/settings.py、tests/test_android_reverse_tool.py、crawagent/prompts/system.md |

---

## 〇、价值

- **改动前**：adb 装在 `%LOCALAPPDATA%\Android\Sdk\platform-tools\`（Android Studio 附带，不在 PATH）时，`list_adb_devices` 返回 `ERR: adb 未安装`。逆向实战会话（2026-09-19 导出审查）里 agent 为绕开它，**91 次调用 run_custom_script 手拼 adb.exe 绝对路径**，专用工具几乎闲置（list_adb_devices 2 次、install_apk 1 次、frida 系 0 次），还附带 17+ 次脚本超时重试。
- **改动后**：`_resolve_binary` 三级查找——PATH → .env 显式配置（`ADB_PATH`/`FRIDA_PATH`）→ 常见位置扫描（Android SDK、雷电/MuMu/Nox 模拟器）。装了 Android Studio 或模拟器的机器开箱即用；`list_adb_devices` 首调即成功，agent 走专用工具链而不是脚本绕行。

## 一、背景与问题

1. `_check_binary` 只做 `shutil.which(name)`——只认 PATH。Windows 上 adb 的主流安装方式（Android Studio / 独立 SDK）都不进 PATH。
2. 模拟器自带 adb（雷电 `LDPlayer9/adb.exe`、MuMu 12 `shell/adb.exe`、Nox `bin/adb.exe`）同样不在 PATH，且版本各自独立。
3. agent 的替代路线成本高：run_custom_script 每次 subprocess 手拼绝对路径，脚本 120s（当时）超时重试、产物路径失控（APK 被拉到桌面）。
4. `.env` 无显式配置项，用户无法指路非标安装位置。

## 二、目标

1. `android_reverse_tool.py` 新增 `_resolve_binary(name)`：`shutil.which` → settings 显式配置 → 常见位置扫描，命中缓存、miss 不缓存（装完软件即时生效）。
2. `settings.py` 加 `adb_path` / `frida_path` 两个可选 str 字段（空 = 不启用显式配置；.env 生效）。
3. `_run_adb` / `_run_frida` / 六个工具的缺二进制检查全部换 `_resolve_binary`；ERR 串提示 `.env ADB_PATH`。
4. **不做**：macOS/Linux 常见位置表（POSIX 的 which 通常已覆盖，主环境是 Windows）；前端设置页卡片（改 .env 即可，二期可加高级页编辑）；frida-server 设备端部署自动化。

## 三、方案设计

- **查找顺序**（首中即返）：① `shutil.which(name)` / `which(name + ".exe")`；② 显式配置 `settings.{adb_path,frida_path}`（`Path(p).is_file()` 校验通过才认）；③ 常见位置表逐个 `Path.is_file()`；全 miss → None。
- **常见位置表**（仅 adb，frida 靠 pip 装进 venv Scripts、which 已覆盖）：
  - `%LOCALAPPDATA%\Android\Sdk\platform-tools\adb.exe`
  - `C:\Android\Sdk\platform-tools\adb.exe`
  - `%ProgramFiles%\Netease\MuMuPlayer-12.0\shell\adb.exe`、`%ProgramFiles(x86)%` 同名
  - `C:\LDPlayer\LDPlayer9\adb.exe`、`%ProgramFiles%\LDPlayer\LDPlayer9\adb.exe`
  - `%ProgramFiles%\Nox\bin\adb.exe`
- **映射**：`adb → adb_path`；`frida` / `frida-trace` → `frida_path`（同目录）。
- **缓存**：模块级 dict 缓存命中结果（进程内不重复扫盘）；miss 不缓存。
- **ERR 串**：`ERR: adb 未安装（装 platform-tools，或在 .env 设 ADB_PATH 后重试）`——返回值给安装提示是设计意图（013 教训：提示放返回值，不放进工具描述教唆预判）。

## 四、涉及文件清单

| 操作 | 文件 | 说明 |
|------|------|------|
| 修改 | crawagent/tools/android_reverse_tool.py | `_resolve_binary` + 常见位置表 + 缓存；`_run_adb`/`_run_frida`/六工具接线 |
| 修改 | crawagent/config/settings.py | `adb_path` / `frida_path` 可选字段 |
| 修改 | tests/test_android_reverse_tool.py | 新增 _resolve_binary 用例（which/显式/常见位置/miss/缓存）+ list_adb_devices fallback 端到端 |
| 修改 | crawagent/prompts/system.md | list_adb_devices 条目补"自动定位"能力句 |

## 五、验证方式

1. which 优先：monkeypatch `shutil.which` 返回假路径 → `_resolve_binary` 原样返回。
2. 显式配置命中：fake settings.adb_path 指向 tmp 假文件 → 返回它；指向不存在路径 → 跳过该级。
3. 常见位置命中：monkeypatch 位置表造 tmp 假文件 → 返回；连续两次调用走缓存（计数器验证 which 只调一次）。
4. 全 miss → None；`list_adb_devices` 返回串含 `ADB_PATH` 提示。
5. 端到端：monkeypatch `_resolve_binary` + 假 `adb devices -l` 输出 → 列出设备。
6. 全量回归：`uv run pytest tests/ -q` 全绿。

## 六、后续可扩展（不在本次范围）

- 前端高级设置页加 adb/frida 路径编辑卡片（同产物目录编辑模式）。
- macOS/Linux 常见位置表（brew cask 路径等）。
- `frida-server` 版本匹配检测与自动 push（`push_file` 已有能力之上的编排）。

## 七、实施顺序建议

settings 字段 → `_resolve_binary` + 接线 → 测试 → system.md 一句话 → 全量回归。

---

## 八、实施记录（2026-09-19）

### 怎么做到的（人话版）

核心就是给"找二进制"这条路径加了两个兜底层。原 `_check_binary` 只有 `shutil.which` 一层；新 `_resolve_binary` 先 which（PATH 命中最快），再问 settings 里用户显式指的路（`.env` 写 `ADB_PATH=...`），最后扫一张 Windows 常见位置表（Android SDK + 三大模拟器）。命中结果进程内缓存——扫描磁盘只发生一次；miss 故意不缓存，否则用户装完 adb 还得重启才生效。frida 不设位置表：它靠 pip 装进 venv Scripts，which 天然覆盖，设表是过度工程。

六个 android 工具的缺二进制检查统一走 `_resolve_binary`，ERR 提示串同步加"或设 ADB_PATH"。settings 两个字段空串默认——不写 .env 时行为与改前完全一致。

### 实际改动

| 操作 | 文件 | 说明 |
|------|------|------|
| 修改 | crawagent/tools/android_reverse_tool.py | `_resolve_binary`（三级查找 + 命中缓存）+ `_ADB_COMMON_LOCATIONS` 表 + `_run_adb`/`_run_frida`/六工具接线 |
| 修改 | crawagent/config/settings.py | `adb_path: str = ""` / `frida_path: str = ""` |
| 修改 | tests/test_android_reverse_tool.py | +7 用例（显式配置三级/常见位置表/缓存/ERR 串 ADB_PATH 提示/devices fallback 端到端） |
| 修改 | crawagent/prompts/system.md | list_adb_devices 条目补自动定位能力句 |

### 验证结果（对照 §五）

1-5 全部 ✓（monkeypatch which / tmp 假 exe / 位置表命中 / miss 提示 / devices 端到端），6 全量 347 pytest 全绿（330 基线 + 10 session_dir + 7 android）。

### 说明

- 显式配置优先级低于 which：PATH 里的 adb 恒胜出，`.env` 只在 PATH 没有时兜底——避免"用户指了旧版本还纳闷为什么不生效"的反向困惑。
- 本变更已随本 commit 入库（代码与文档同批）。

### 追加记录：端到端验证修复两处缺口（2026-09-19，commit 后补）

015 入库后做端到端验证（PATH 无 adb 的真机环境），发现两处缺口并当场修复：

1. **解析路径没用于执行**：`_check_binary` 三级查找命中常见位置表拿到绝对路径，但 `_run_adb` 执行时仍写死 `["adb", ...]`、`_run_frida` 仍用裸 `args[0]`——PATH 无 adb 时照样 WinError 2，三级查找形同虚设（文档"接线"一节表述与实际不符，实为只接了判存检查）。修法：subprocess 首元素改用解析出的绝对路径。
2. **adb daemon 启动行被当设备**：`list_adb_devices` 解析 `devices -l` 输出时，`* daemon started successfully` 等 adb 提示行满足"≥2 列"被计入，首启环境报"找到 2 台设备"（serial=`*`）。修法：只认第二列为合法设备状态（device/offline/unauthorized/recovery/sideload）的行。

回归：android 工具测试新增 3 用例（adb/frida 执行路径断言 + daemon 行过滤），全量 350 pytest + 10 vitest 全绿。端到端实测：`_check_binary("adb")` 命中 `%LOCALAPPDATA%\Android\Sdk\platform-tools\`，`list_adb_devices` 无设备时正确返回 NO_DEVICE。
