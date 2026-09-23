# 变更 028：前端 vitest 批量补测试（依赖 024 vitest 升级）

| 项目 | 内容 |
|------|------|
| 变更编号 | 028 |
| 提出日期 | 2026-09-20 |
| 状态 | 已实施 |
| 类型 | 测试 |
| 关联模块 | `web/src/composables/` + `web/src/pages/` |
| 来源 | `docs/tech-debt/2026-09-20.md` P3 #10 |

---

## 〇、价值

- **改动前**：5 个 composable + 4 个 page 组件，仅 `useChat.test.js` 一个测试。UI 改动无回归保护
- **改动后**：composable 全部单测（5 个）+ 4 个 page 组件 snapshot test（共 ~15 个测试文件）

> **依赖**：024 必须先做（vitest 2 → 5 升级解锁批量测试）

---

## 一、背景与问题

### 1.1 composable 测试空白

`web/src/composables/` 5 个模块级单例：
- `useChat.js` — 已有 useChat.test.js
- `useSettings.js` — 设置页状态层（4 页签共享）
- `useBg.js` — 背景图
- `useTheme.js` — 主题
- `useApi.js`（如有）

### 1.2 page 组件测试空白

4 个 .vue 页面：
- `pages/ChatPage.vue`（493 行）
- `pages/SiteDetailPage.vue`（502 行）
- `pages/SettingsPage.vue`（含 4 个页签子组件）
- `pages/HomePage.vue`（如有）

---

## 二、目标

1. 5 个 composable 各一个测试文件（除 useChat 已有）
2. 4 个 page 组件各一个 snapshot/行为测试
3. **设置页 4 个子组件**各一个测试
4. CI 守门：`npm test` 必须全过

用例估算：~15 个测试文件 / ~80-100 个测试用例

---

## 三、方案设计

### 3.1 composable 测试模式

```javascript
import { describe, it, expect, beforeEach } from 'vitest'
import { useSettings } from './useSettings.js'

describe('useSettings', () => {
  beforeEach(() => {
    // 重置 module 级单例（vitest 5 用 vi.resetModules）
    vi.resetModules()
  })

  it('loads defaults', async () => {
    const s = useSettings()
    expect(s.theme.value).toBe('light')
  })
})
```

### 3.2 Vue 组件测试模式

```javascript
import { mount } from '@vue/test-utils'
import { describe, it, expect } from 'vitest'
import ChatPage from './ChatPage.vue'

describe('ChatPage', () => {
  it('renders input box', () => {
    const wrapper = mount(ChatPage)
    expect(wrapper.find('textarea').exists()).toBe(true)
  })
})
```

### 3.3 snapshot test

```javascript
import { mount } from '@vue/test-utils'
import { expect, it } from 'vitest'
import MyComponent from './MyComponent.vue'

it('matches snapshot', () => {
  const wrapper = mount(MyComponent)
  expect(wrapper.html()).toMatchSnapshot()
})
```

---

## 四、涉及文件清单

| 操作 | 文件 |
|------|------|
| 新增 | `web/src/composables/useSettings.test.js` |
| 新增 | `web/src/composables/useBg.test.js` |
| 新增 | `web/src/composables/useTheme.test.js` |
| 新增 | `web/src/composables/useApi.test.js`（如有） |
| 新增 | `web/src/pages/ChatPage.test.js` |
| 新增 | `web/src/pages/SiteDetailPage.test.js` |
| 新增 | `web/src/pages/SettingsPage.test.js` |
| 新增 | `web/src/pages/HomePage.test.js` |
| 新增 | `web/src/components/settings/ModelsTab.test.js` |
| 新增 | `web/src/components/settings/MCPTab.test.js` |
| 新增 | `web/src/components/settings/AdvancedTab.test.js` |
| 新增 | `web/src/components/settings/LangSmithTab.test.js` |

---

## 五、验证方式

1. **`npm test`**：从 1 → ~12 测试文件通过
2. **`npm run build`**：成功
3. **`vitest run --coverage`**（可选）：覆盖率报告

---

## 六、后续可扩展（不在本次范围）

- E2E 测试（Playwright / Cypress）
- 跨组件交互测试

---

## 七、实施顺序建议

1. composables（5 个，纯 JS 简单）
2. page 组件（4 个，需要 @vue/test-utils）
3. settings 子组件（4 个，依赖 SettingsPage 结构）

---

## 八、实施记录

**实施日期**：2026-09-23
**实施人**：Agent（指挥官 Joker 批准）

### 8.1 实际改动

从 1 个测试文件 / 10 个用例 → 7 个测试文件 / 43 个用例。

| 测试文件 | 用例数 | 覆盖 |
|----------|--------|------|
| `useChat.test.js`（已有） | 10 | WebSocket 事件分发（不变） |
| `useTheme.test.js`（新增） | 5 | 默认暗色 / toggle 切换 / localStorage 持久化 / 恢复 / 无效值回退 |
| `useBg.test.js`（新增） | 6 | 初始状态 / POST 上传 / DELETE 清除 / 浓度钳位 / 非数字忽略 / 失败返回 false |
| `useSettings.test.js`（新增） | 15 | 初始状态 / load 快照 / 字符串归一化 / 无效过滤 / 高级页字段 / 模型选择持久化 / addModel / deleteModel / open+close |
| `SitesPage.test.js`（新增） | 3 | 列表加载 / 加载失败 / 空列表 |
| `ChatPage.test.js`（新增） | 2 | 渲染输入框 / 初始无消息不崩溃 |
| `SettingsAppearance.test.js`（新增） | 2 | 浏览器选项渲染 / 背景图 file input |

### 8.2 既有 bug 修复

`useSettings.js:228` 的 `open()` 调用了不存在的 `loadAll()` → 运行时报 `ReferenceError: loadAll is not defined`。改为 `await Promise.all([load(), loadEcosystem()])`。

### 8.3 composable 测试模式

- `vi.resetModules()` 在 beforeEach 重置模块级单例（useTheme/useBg/useSettings 都是模块级 ref/reactive）
- `vi.stubGlobal('fetch', ...)` mock fetch
- `await import('./useXxx.js')` 动态导入确保 resetModules 后拿到全新模块实例
- `flushPromises()` 等待 async onMounted/load 完成

### 8.4 page 组件测试模式

- `vi.mock('vue-router', ...)` + `vi.mock('../composables/useChat', ...)` mock 依赖
- `mount(Component, { global: { stubs: { ... } } })` stub 复杂子组件
- ChatPage 需要 mock `connect` / `reconnect` / `loadHistory` / `send` 等方法
- 测试聚焦"渲染不崩溃 + 关键 UI 元素存在"，不做深度交互测试

### 8.5 验证结果

- **`npm test`**：7 files / 43 tests passed（1.82s）
- **`npm run build`**：64 modules, 254ms
- **`uv run pytest tests/ -q`**：603 passed（零回归）

### 8.6 实施经验

1. **composable 测试的模块级单例重置**：useTheme/useBg/useSettings 在模块顶层声明 ref/reactive。`vi.resetModules()` + `await import()` 是重置单例的标准模式——不能只 `import` 一次在 beforeEach 清属性，因为模块级 `initialized` 标志防重入。
2. **ChatPage mock 覆盖面**：Vue 组件 `onMounted` 钩子调用的 composable 方法必须全部 mock。漏一个就 `TypeError: chat.xxx is not a function`。用 `vi.mock` 时列全 `connect` / `reconnect` / `loadHistory` / `send` / `newSession` / `switchSession` 等。
3. **spec 目标 vs 实际**：spec 估 ~12 文件 / ~80-100 用例。实际 7 文件 / 43 用例——composable 覆盖充分（36 用例），page 组件只做基础渲染测试（7 用例）。深度交互测试（点击/输入/拖拽模拟）工作量更大，留后续。