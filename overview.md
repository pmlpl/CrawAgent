# CrawAgent WebUI（Vue 3 版）— 交付概览

## 做了什么

为 CrawAgent 爬虫智能体打造了 **Vite + Vue 3 工程版 WebUI**，设计语言「丝与网」——Spider 沿丝爬行（工具调用即爬行轨迹），最终织成信息网（结果）。前端为组件化 SFC 工程，后端 FastAPI 托管构建产物。

## 文件结构

```
web/                              # Vue 3 工程（Vite 构建）
├── package.json / vite.config.js # 依赖与构建配置（dev 代理 /api、/ws → 8000）
├── index.html                    # 入口（字体、meta）
└── src/
    ├── main.js / style.css       # 启动文件 / 设计令牌（明暗双主题 CSS 变量）
    ├── App.vue                   # 消息流渲染、自动滚动、状态栏常驻、失败草稿恢复
    ├── composables/
    │   ├── useTheme.js           # 主题单例（localStorage 持久化）
    │   ├── useChat.js            # 会话状态 + WebSocket 事件流（核心）
    │   └── useSettings.js        # 设置面板：加载/测试/保存 API Key 与模型
    └── components/
        ├── TopBar.vue            # 品牌、会话 chip、新会话、设置、主题切换
        ├── WebCanvas.vue         # 「活蛛网」canvas 动效（记忆点）
        ├── EmptyState.vue        # 空状态 + 建议任务 chips
        ├── CrawlTrace.vue        # 工具调用 → 可折叠爬行轨迹（默认收起）
        ├── Thinking.vue          # AI 推理/思考内容（默认收起）
        ├── ChatComposer.vue      # 输入区（自动增高、IME 保护、spinner）
        └── SettingsModal.vue     # 设置弹窗（API Key/Base URL/模型 + 测试连接）

crawagent/web/server.py           # FastAPI：托管 web/dist + 三个 settings API
crawagent/web/static/index.html   # 旧版单文件页（保留作降级回退）
```

## 关键决策

- **复用后端**：FastAPI + WebSocket 事件协议不变，与 CLI 共享 `data/sessions.db`（session_id 两端互通）；`_index_file()` 请求时动态解析，构建完成无需重启服务。
- **组件化**：useChat 组合式函数集中管理 WS 连接、事件→消息流映射、历史恢复、新会话；轨迹用深层响应式对象，工具结果到达时自动挂载到对应节点。
- **设计语言保留**：蛛网动效、爬行轨迹、终端同款状态栏、明暗双主题、Space Grotesk / JetBrains Mono / Noto Sans SC。
- **细节**：出错保留输入（watch error 条目 → 草稿回填）、断线横幅点击重连、中文输入法 Enter 保护、prefers-reduced-motion 降级、移动端单列 + 44px 触摸目标。

## 使用方式

```bash
# 生产模式（当前已运行）
cd web && npm run build                 # 构建到 web/dist
../.venv/Scripts/python.exe -m crawagent.web.server   # http://127.0.0.1:8000

# 开发模式（热更新）
npm run dev                             # http://127.0.0.1:5173，/api 与 /ws 自动代理到 8000
```

## 验证结果

- `npm run build` 成功（dist: index.html + js 145KB + css 11KB）
- 页面 200 且引用 Vue 构建资源；`/assets/*.js|.css` 均 200
- WebSocket 冒烟通过：`ai → status → done` 完整事件序列；历史接口正常

## 第三轮新增（会话侧栏 + token 级流式）

- **`GET /api/sessions`**：从 `sessions.db` 的 checkpoints 表列出全部会话（按最近活跃排序，含最后一条消息 60 字预览）。CLI 里聊过的会话也会出现在侧栏，可直接切换继续。
- **token 级流式**：`stream_mode` 改为 `["messages", "values"]` 双模式——`messages` 产出 `ai_delta` 增量（逐字上屏，带闪烁光标），`values` 仍负责工具调用/结果与指标；用 `msg.id` 配对去重，非流式模型自动回退整段 `ai` 事件。
- **`SessionSidebar.vue`**：桌面端 250px 固定左栏，移动端（<860px）为抽屉 + 遮罩，顶栏汉堡按钮开关；每轮结束自动刷新列表预览。
- 验证：`/api/sessions` 返回真实 CLI 会话（含 B 站下载任务）；WS 冒烟 45 个 `ai_delta` → `ai_done` → `status` → `done` 序列完整。

## 后续可选

- Pinia 状态管理（规模扩大后）、Vitest 组件测试、会话删除/重命名

## 第五轮新增（设置面板 + 折叠 + 状态栏下移）

- **设置面板** `SettingsModal.vue` + `useSettings.js`：
  - `GET /api/settings` 读取当前配置，API Key 脱敏返回（`sk-d****...7f58`）。
  - `POST /api/settings` 写 `.env`（保留注释与其他键），并失效 `_agent` / `_checkpointer` 单例，下次对话按新配置重建。
  - `POST /api/settings/test` 用新配置对 ping 调一次 `ChatOpenAI.invoke`，不影响当前 Agent。
  - UI：API Key 密码框带眼睛切换（已配置时 placeholder 提示）、Base URL / 模型 输入、测试连接 + 保存 分离按钮、结果提示徽章。
- **轨迹与思考折叠**：
  - `CrawlTrace.vue` 重构为可折叠，默认收起，头部一行 `▸ 爬行轨迹 · crawl trace · 3 次工具调用`，展开后渲染原节点时间线。
  - 新增 `Thinking.vue`：默认收起、虚线边、accent 色头部、表情 + 思考标签；展开后 max-height 360px 内部滚动。
  - 后端从 `AIMessage.additional_kwargs.reasoning_content` 提取推理内容（DeepSeek-R1 等），通过 `ai_thinking` 事件下发；非推理模型不触发，UI 无差异。
- **状态栏下移**：移除聊天流中的 `statusline` 渲染，改为 `lastStatus` 响应式 ref 常驻在 Composer 下方——monospace、accent dot、单行省略号，每次 turn 自动更新。
- **清理**：移除 `ChatComposer` 的 `silk → web → knowledge` 装饰文字与 `.silk-note` 样式。
- **布局**：保留 App Shell 形态（侧栏静态 + 右侧独立滚动 + 输入框固定列底）。
- 验证：build 91KB js + 20KB css；GET `/api/settings` 返回脱敏；POST /api/settings 正确失效单例；WS 冒烟 125 `ai_delta` → `ai_done` → `status` → `done` 序列完整。

## 第六轮修复（状态栏按会话记忆 + Key 覆盖 Bug）

- **状态栏按会话记忆**：`lastStatus` 改为 `computed(() => statusBySession[session.value])` 映射（切换会话不丢失，切回来立即恢复）；`/api/history` 新增 `status` 字段——内存指标存在用 `metrics.status_line()`（含耗时/性能），服务重启/CLI 会话则用 `_reconstruct_status()` 从检查点重建（轮数/步数/token/缓存命中）。
- **设置面板 Key 覆盖 Bug 修复**：原守卫只拦截 `****` 前缀，脱敏掩码 `sk-d****...7f58` 被回写覆盖真实 Key。现在后端 POST/GET/test 与前端均以「含 `*` 即视为掩码」判断，杜绝回写；损坏的 Key 已被清空，重新在设置面板填写即可恢复（空 Key 时历史/会话接口返回空属预期行为）。
