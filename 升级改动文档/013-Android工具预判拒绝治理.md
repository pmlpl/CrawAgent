# 变更 013：Android 工具预判拒绝治理（不许凭依赖提示甩锅）

| 项目 | 内容 |
|------|------|
| 变更编号 | 013 |
| 提出日期 | 2026-09-18 |
| 状态 | 已完成（2026-09-18） |
| 类型 | 契约加固（prompt-only） |
| 关联模块 | crawagent/prompts/system.md |

---

## 〇、价值

- **改动前**：用户让 CrawAgent 逆向分析某 App 播放源。agent 没调 `list_adb_devices()` 就直接回复"目前系统未安装 ADB，无法直接连接设备"+ 一整页环境准备教程（装 ADB / root 设备 / frida-server 部署 / 八步分析流程表）。工具本身在 ADB 缺失时返回精确串 `"ERR: adb 未安装（装 platform-tools 后重试）"`，agent 本该调一次拿这个串，却因 system.md 的依赖提示和软 Step 0 预判拒绝 + 甩锅。
- **改动后**：system.md 删掉 `list_adb_devices` 描述里的前置依赖提示、Android Step 0 加硬规则禁止预判、新增通用 `ENVIRONMENT PREREQUISITE RULE`。agent 遇系统二进制依赖工具（adb/frida/playwright 等）一律调了再说，拿真实状态串照原样上报 + `ask_user` 问下一步，禁止写教程式长清单。

## 一、背景与问题

1. **描述里的依赖提示教唆预判**：system.md 第 44 行 `list_adb_devices()` 描述结尾 `Requires ADB installed on the host.` —— agent 读到"需要 ADB"，先入为主认为"没装就别调"，省掉工具调用直接拒绝。工具实现本身优雅降级（`_check_binary("adb")` 返回 None → 返回 ERR 串），描述却提前把判定的活抢了。
2. **Android Step 0 太软**：只写"list_adb_devices() 确认设备在线 + 已 root"，没写 MUST 调、不许预判。agent 钻空子：它"知道"要 ADB、又没硬规则逼它调，就省了工具调用直接写教程。
3. **附带违反两条已有硬规则**：`CONCISE TOOL USE`（回复写成博客教程，不是 ONE consolidated final answer）、`TOOL-USE DECISION`（本该调工具却用纯文本拒绝）。
4. **MCP 侧早有同类契约**：006 变更给 MCP Capture Workflow 加了 Step 0 工具备查契约（"工具缺失不等于放弃，MUST 调 check_mcp_status 诊断"）。Android 侧没有同等硬规则，是契约盲区。

## 二、目标

1. 删掉 `list_adb_devices` 描述里的 `Requires ADB installed on the host.`，改成"缺 ADB / 无设备 / 未 root 都返回精确状态串，不许预判——调它"。
2. Android Step 0 加 HARD：不许凭"反正要 ADB/frida"预判拒绝、不许跳过调用直接写教程；拿到 ERR/NO_DEVICE 后简洁报告 + `ask_user` 问下一步。
3. 新增通用 `ENVIRONMENT PREREQUISITE RULE`：凡工具带系统二进制依赖（adb/frida/frida-trace/playwright/chromedriver），不许预判，调了再说，工具自带优雅降级。
4. **不做**：改工具实现（工具已优雅降级，无需动）；改工具数（43 不变）；改前端。

## 三、方案设计

- **描述去依赖提示**：`list_adb_devices` 描述末尾从"Requires ADB installed on the host."换成"Missing ADB / no device / not rooted all return a specific status string you report verbatim; do NOT pre-judge availability — call it."—— 把判定权还给工具。
- **Step 0 加硬规则**：在 Android 逆向 Workflow 的 Step 0 加 `(MUST)` 标 + HARD 段，明确"不许凭反正要 ADB/frida 预判拒绝、不许跳过调用直接写教程；工具返回精确状态串，你照原样上报 + ask_user 问下一步"。
- **通用规则**：在 Other rules 区加 `ENVIRONMENT PREREQUISITE RULE (HARD)`，覆盖所有带系统二进制依赖的工具，与 006 的 MCP 工具备查契约呼应（MCP 侧管"工具不在列表"，本规则管"工具在列表但系统二进制可能缺"）。

## 四、涉及文件清单

| 文件 | 改动 |
|------|------|
| crawagent/prompts/system.md | 三处：①第 44 行 `list_adb_devices` 描述删依赖提示换"调了再说" ②第 345 行 Android Step 0 加 MUST+HARD ③Other rules 区加 `ENVIRONMENT PREREQUISITE RULE` |

无代码改动，无新增测试（prompt-only；行为正确性靠人工对话验证，已有 329 pytest + 10 vitest 不受影响）。

## 五、实施记录（人话版）

1. **`list_adb_devices` 描述（system.md 第 44 行）**：删 `Requires ADB installed on the host.`，换成 `Missing ADB / no device / not rooted all return a specific status string you report verbatim; do NOT pre-judge availability — call it.`。把判定权从描述还给工具。
2. **Android Step 0（system.md 第 345 行）**：标题加 `(MUST)`，正文加 HARD 段："不许凭'反正要 ADB/frida'预判拒绝、不许跳过调用直接写教程——工具返回精确状态串（`ERR: adb 未安装...` / `NO_DEVICE: ...` / `找到 N 台设备`），你照原样上报 + ask_user 问下一步（提供 APK / 装 ADB / 连设备）。拿到 ERR/NO_DEVICE 后简洁报告一句 + ask_user，不写博客式长清单。"
3. **通用 ENVIRONMENT PREREQUISITE RULE**：Other rules 区加一条 HARD："凡工具带系统二进制依赖（adb/frida/frida-trace/playwright/chromedriver），不许预判'没装就别调'。工具自带优雅降级——缺二进制返回 `ERR: X 未安装（<安装提示>后重试）` 精确串。你 MUST 调工具拿真实状态串，照原样上报 + ask_user 问下一步，禁止凭描述里的依赖提示预判拒绝或写教程式长清单。"与 006 MCP 工具备查契约呼应（MCP 侧管工具不在列表，本规则管工具在列表但二进制可能缺）。

## 六、验证

- `uv run pytest tests/ -q` → 329 passed（prompt-only，无代码改动，基线不变）
- `cd web && npm test` → 10 passed（前端无改动）
- 行为验证：重新问 agent 逆向任务，应调 `list_adb_devices()` 拿真实状态串而非预判拒绝 + 教程
