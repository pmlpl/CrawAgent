# 变更 028：前端 vitest 批量补测试（依赖 024 vitest 升级）

| 项目 | 内容 |
|------|------|
| 变更编号 | 028 |
| 提出日期 | 2026-09-20 |
| 状态 | 待批准（依赖 024）|
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

> 待批准后由 Agent 实施时填写。