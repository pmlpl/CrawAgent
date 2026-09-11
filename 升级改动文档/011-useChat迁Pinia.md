# 011 — useChat 迁 Pinia（单 setup store，不拆分）

## 背景

P2-4。`useChat.js` 已 617 行（清单写 317，过时），单例经模块级 `_instance` 缓存包闭包工厂 `createChat()`：15 个 reactive + 7 个闭包私有非响应句柄（`ws`/`tickTimer`/`sessionPoll`/`currentTrace`/`traceRoundId`/`aiStreams` Map/`sessionId`）+ 2 个 setInterval + WS 处理器。8 个消费方全用 `useChat()`+`.value` 访问。仓库无 Pinia 足迹。

**目标**：治「单例 composable 是代码味」+ 拿 devtools + 标准 store 契约 + 可测性，**不缩小文件**（拆 store 是另一回事，YAGNI 留到真有第二组职责）。

## 设计决策（grill-with-docs 共识 + 实施期修正）

1. **单 setup store，不拆分**：保持 617 行现状结构，只把闭包工厂包进 `defineStore`。拆多 store（session/ws/items）行为风险高（`aiStreams` Map 身份比较、`currentTrace` 跨步骤、WS+定时器闭包私有），不在本变更范围。
2. **setup store 形态**（非 options store）：`defineStore('chat', () => { ...617 行... return {...} })`。`ws`/`tickTimer`/`sessionPoll`/`currentTrace`/`traceRoundId`/`aiStreams` Map 作为 setup 函数内的 `const`/`let` 局部变量，天然保非响应私有——这是 options store 塞不进的，setup store 是闭包工厂的自然映射。
3. **消费方改访问形态（实施期修正）**：原规格假设「薄封装、消费方零改动」——**这是错的**。Pinia setup store 代理对 ref **自动解包**：`chat.busy` 直接是值（boolean），`chat.busy.value` 变 `undefined`。故 8 个消费方所有 `chat.X.value` 须去掉 `.value`（script 和 template 都一样）；`chat.items` 是 reactive 数组，Pinia 直接暴露，`.length`/`.find` 不变。唯一特殊是 ChatComposer 的 `draft`（可写 computed + v-model + `.value`），用 `storeToRefs(chat)` 拿 ref 形态保 v-model 与 `.value` 都能用。
4. **测试隔离改 `setActivePinia(createPinia())`** + 断言去 `.value`：现 10 vitest 用 `vi.resetModules()`+动态 import 拿新单例，Pinia 下不灵。改 `beforeEach` 加 `setActivePinia(createPinia())` 拿全新 store，静态 import。**断言里的 `.value` 也要去**（`chat.busy.value`→`chat.busy`，读写都走 Pinia 直接收/赋值）。
5. **文件位置不动**：`useChat.js` 留在 `web/src/composables/`（不挪 `stores/`），省 import 路径改动。
6. **`useChat()` 薄封装保留**：`export function useChat() { return useChatStore() }`——消费方仍调 `useChat()`，内部返 store。虽消费方访问形态改了，但调用入口不变。

## 改动清单

### `web/package.json`
- 加依赖 `pinia`（latest，~2.x；与 vue 3.x 兼容）

### `web/src/composables/useChat.js`
- 顶部 `import { defineStore } from 'pinia'`
- 现有 `let _instance = null; export function useChat() {...}` 三行删除
- 现有 `function createChat() { ... return {...} }` 改为 `const useChatStore = defineStore('chat', () => { ...同体...; return {...} })`
- 文件末加薄封装：`export function useChat() { return useChatStore() }`
- **内部代码零改动**：所有 ref/reactive/computed、`ws`/定时器/`aiStreams` Map/`currentTrace` 等局部变量、`handleEvent`/`endTurn`/`push*`/`connect`/`send` 等函数原样保留在 setup 函数体内；`return` 块原样导出同一组 state+actions
- 构造期副作用（`persistSession()` 末尾调用 `:608`）保留——Pinia setup store 在首次 `useChatStore()` 时跑函数体一次，等价

### `web/src/main.js`
- `import { createPinia } from 'pinia'`
- `const app = createApp(App).use(createPinia()).use(router)`——**Pinia 必须在 `useChat()` 调用前装**（`:12` 行的 `useChat()` 现在会触发 store 创建，无 active pinia 会抛 `getActivePinia()`）

### `web/src/composables/useChat.test.js`
- `beforeEach` 删 `vi.resetModules()`，改加 `setActivePinia(createPinia())`
- `setup()` 动态 import 改静态 `import { useChat }`
- 断言去 `.value`（`chat.busy.value`→`chat.busy` 等），10 个用例的事件分发断言全保留为安全网

### 8 个消费方（去 `.value`）
- `App.vue`：`chat.sessions.value`/`chat.session.value`→去 `.value`
- `ChatPage.vue`：`chat.connected/typing/lastDraft/runningElapsed/currentProgress/busy` 去 `.value`（多处 template+script）
- `ChatComposer.vue`：加 `storeToRefs` 拿 `draft` 保 ref 形态；`chat.lastStatus.value`→去 `.value`
- `ContextRing.vue`：`chat.busy.value`→`chat.busy`（watch + 判断）
- `SitesPage.vue`/`SiteDetailPage.vue`：`chat.connected`/`chat.lastDraft =` 去 `.value`
- `AskCard.vue`/`main.js`：无 `.value` 访问，零改动

## 不做

- 不拆多 store（session/ws/items）——行为风险高，YAGNI
- 不迁 useSettings（本变更只动 useChat）
- 不挪文件到 `stores/`（省 import 改动）
- 不加 devtools 额外配置（Pinia 自带）
- 不改 system.md（纯前端架构）

## 验收

- `npm test` 10 vitest 全绿（setActivePinia + 断言去 .value）
- `npm run build` 通过
- 225 pytest 不受影响（纯前端）
- 启动后聊天页功能正常：发消息、收流式、工具轨迹、ask_user、重连重放、新/切/删会话

## 风险与回滚

- **主风险**：setup store 内 `aiStreams` Map 与 `items` reactive proxy 的身份比较逻辑（现按 id 删不按身份比，见 `vue-reactive-proxy-identity` 记忆）——setup store 内 `aiStreams` 仍是普通 Map、`items` 仍是 reactive，逻辑不变。10 个 vitest 含 `用例 4d`（dedupe）专门 pin 这条。
- **回滚**：失败就 revert 单 commit；useChat.js 结构没动，回滚零成本。

## 实施记录（人话版，2026-09-11）

- **打脸点**：grill 第 1 轮据探索 agent 结论推荐「薄封装、消费方零改动」——**实施时被 vitest 打脸**：Pinia setup store 代理对 ref **自动解包**，`chat.busy.value` 变 `undefined`，3 个测试挂。修正：消费方所有 `chat.X.value` 去 `.value`（Pinia 直接收值），ChatComposer 的 `draft`（可写 computed + v-model）用 `storeToRefs` 保 ref 形态。原「零改动」前提是假的，扩到改 8 个消费方——已向指挥官汇报并获批准拥抱方案。
- **useChat.js**：`createChat()` 闭包体逐字包进 `defineStore('chat', () => {...})`，`ws`/`tickTimer`/`sessionPoll`/`currentTrace`/`aiStreams` Map 等作为 setup 局部变量保非响应私有，行为零改动；末尾 `export function useChat() { return useChatStore() }` 薄封装。
- **main.js**：`createPinia()` 必须在 `useChat()` 前装（store 创建时需 active pinia），`.use(createPinia())` 链在 `.use(router)` 前。
- **测试**：`vi.resetModules()`+动态 import → `setActivePinia(createPinia())`+静态 import；断言去 `.value`（`chat.busy=...`/`expect(chat.busy)`/`chat.session`）。10 个事件分发用例全保留为安全网，含 `用例 4d` dedupe。
- **踩坑**：消费方 template 里 `v-if="chat.typing.value"` 在旧单例下能跑（`chat` 是普通对象，模板不自动解包嵌套 ref，故需 `.value`）；迁 Pinia 后 `chat` 是 store 代理自动解包，`chat.typing` 即值，`.value` 必须去掉。script 同理。唯一保 `.value` 的是 ChatComposer 的 `draft`（`storeToRefs` 拿回 ref）。
- 基线 225 pytest + 10 vitest 全绿。
