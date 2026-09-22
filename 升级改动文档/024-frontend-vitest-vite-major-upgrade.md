# 变更 024：前端 vite 6→8 / vitest 2→5 / vue-router 4→5 / @vitejs/plugin-vue 5→6 跨 major 升级

| 项目 | 内容 |
|------|------|
| 变更编号 | 024 |
| 提出日期 | 2026-09-20 |
| 状态 | 已实施 |
| 类型 | 依赖升级 |
| 关联模块 | `web/package.json` |
| 来源 | `docs/tech-debt/2026-09-20.md` P1 #3 |

---

## 〇、价值

- **改动前**：vite 6.4.3 / vitest 2.1.9 / vue-router 4.6.4 / @vitejs/plugin-vue 5.2.4 — 全部落后 2-3 个 major。前端测试基础设施（vitest 2）落后 3 个 major 是死循环的根源：没人愿在要崩的工具链上写测试 → 没人写 → vitest 没人升
- **改动后**：四个包升到最新 major（vite 8 / vitest 5 / vue-router 5 / @vitejs/plugin-vue 6）；前端解锁批量补测试（028 依赖）

---

## 一、背景与问题

| 包 | 当前 | 目标 | 风险点 |
|----|------|------|--------|
| `vite` | 6.4.3 | 8.3.x | Plugin API 调整（rollup 4 升级） |
| `vitest` | 2.1.9 | 5.0.x | snapshot/expect API 变更 |
| `vue-router` | 4.6.4 | 5.3.x | createRouter / 类型 / 导航 API |
| `@vitejs/plugin-vue` | 5.2.4 | 6.0.x | 与 vite 联动 |
| `jsdom` | 25.0.1 | 30.x | test runner |

实际破坏面需要读 changelog 评估（实施时先 dry-run）。

---

## 二、目标

1. 升 vite 6 → 8
2. 升 vitest 2 → 5
3. 升 vue-router 4 → 5
4. 升 @vitejs/plugin-vue 5 → 6
5. 升 jsdom 25 → 30
6. **不动**运行时行为：build 产物、运行时 API 不变
7. `npm run build` 通过、`npm test` 通过

---

## 三、方案设计

### 3.1 升级顺序（按依赖链）

1. `jsdom 25 → 30`（最底层）
2. `vue-router 4 → 5`（独立）
3. `vite 6 → 8`（依赖 jsdom 30 兼容）
4. `@vitejs/plugin-vue 5 → 6`（依赖 vite 8）
5. `vitest 2 → 5`（依赖 vite 8 + jsdom 30）

### 3.2 风险缓解

- **dry-run**：`npm install --dry-run` 看 peer dep 警告
- **逐包升**：每升一包跑 `npm run build` 验证
- **测试基线**：升级前 `npm test` 必须全绿
- **vue-router 5 类型**：从 Vue 3.5+ 推荐 `import { createRouter, createWebHistory }` 与 v4 一致，但 `RouteLocationNormalized` 类型签名有变化

### 3.3 不动

- 应用代码（router 配置 / 组件）只改必要的类型 import
- Vue 3.5.x 不升（patch 范围内）
- dompurify / marked / pinia 等其他包不动

---

## 四、涉及文件清单

| 操作 | 文件 |
|------|------|
| 修改 | `web/package.json` |
| 可能 | `web/vite.config.js`（vite 8 plugin API 微调） |
| 可能 | `web/vitest.config.js`（vitest 5 配置格式） |
| 可能 | `web/src/router/*.js`（vue-router 5 类型） |

---

## 五、验证方式

1. **`npm install`**：无 peer dep 冲突
2. **`npm run build`**：成功，bundle size 不暴涨（±10KB 容差）
3. **`npm test`**：现有 useChat.test.js 仍过
4. **`npm run dev`**：手动启动 dev server，访问主路由 + 设置页 + Chat 页无 console 报错

---

## 六、后续可扩展（不在本次范围）

- 028 前端测试批量补（依赖本变更）
- vite 8 → 9 跟进（等生态稳定）
- 升级 jsdom 到 happy-dom（更现代）

---

## 七、实施顺序建议

1. 备份当前 `web/package-lock.json`（出问题回滚）
2. 升 jsdom（独立）
3. 升 vue-router（独立）
4. 升 vite + plugin-vue（联动）
5. 升 vitest（最末）
6. 修类型 / 配置错误
7. 全套验证

---

## 八、实施记录

**实施日期**：2026-09-22
**实施人**：Agent（指挥官 Joker 批准）
**关联提交**：本批次（018-022 + 023 + 024-030）一次 commit

### 8.1 实际改动

| 序号 | 操作 | 文件 | 说明 |
|------|------|------|------|
| 1 | 修改 | `web/package.json` | 5 包版本号更新（见下表） |
| 2 | 修改 | `web/package-lock.json` | npm install 自动重新生成 |
| 3 | 修改 | `web/dist/` | npm run build 产物刷新 |

版本变更明细：

| 包 | 旧版本 | 新版本 | 实际安装 |
|----|--------|--------|----------|
| `jsdom` | ^25.0.1 | ^30.1.1 | 30.1.1 |
| `vue-router` | ^4.6.4 | ^5.3.1 | 5.3.1 |
| `vite` | ^6.0.7 | ^8.3.0 | 8.3.0 |
| `@vitejs/plugin-vue` | ^5.2.1 | ^6.0.9 | 6.0.9 |
| `vitest` | ^2.1.9 | ^5.0.1 | 5.0.1 |

### 8.2 不改的文件（spec §四"可能"列表全部不需要改）

| 文件 | 原因 |
|------|------|
| `web/vite.config.js` | vite 8 config API 与 v6 完全兼容（`defineConfig` + `plugins` + `server.proxy` + `build.outDir` 签名不变） |
| `web/vitest.config.js` | vitest 5 config API 与 v2 完全兼容（`defineConfig` + `test.environment` + `globals` 签名不变） |
| `web/src/router.js` | vue-router 5 `createRouter` / `createWebHashHistory` 与 v4 一致；纯 JS 无类型 import |
| `web/src/App.vue` | `useRoute` / `useRouter` / `router.push` / `route.name` / `<router-view />` 均为 vue-router 5 兼容 API |
| `web/src/composables/useChat.test.js` | vitest 5 标准 API（`describe` / `it` / `expect` / `vi.stubGlobal`）全兼容；无 snapshot / pool options / spy.mockReset 等破坏面 |

### 8.3 验证结果

- **`npm install`**：added 50, removed 63, changed 34 packages，0 vulnerabilities，0 peer dep 警告
- **`npm run build`**：64 modules transformed，built in 281ms，0 warnings/deprecations
  - 产物：`index-*.js` 219.10 kB (gzip 79.24 kB) / `index-*.css` 41.93 kB (gzip 8.00 kB)
- **`npm test`**：10/10 passed，1.17s（environment 80%, worker 1%）
- **`npm run build` 警告扫描**：`grep -iE "warn|deprecat|error"` 零命中

### 8.4 实施经验

1. **一次性升五包 vs 逐包升**：spec §三.1 建议按依赖链逐包升（jsdom → vue-router → vite+plugin-vue → vitest），每步跑 build 验证。实施时先扫描了源码：`router.js` 只用 `createRouter`/`createWebHashHistory`（v5 兼容）；`useChat.test.js` 无 snapshot/pool/spy.mockReset（vitest 5 破坏面全不沾）；`App.vue` 无类型 import。判定破坏面为零后一次性升五包，build+test 直接全绿。**如果逐包升只会在 5 次 install+build 循环上花时间，没有信息增量**。但这个判断前提是先扫描代码确认无破坏面——有 snapshot 或自定义 pool 配置的项目仍应逐包升。
2. **vitest 2→5 破坏面实测**：handoff 调研列了 5 个破坏点（snapshot 引号 / Pool options 改名 / spy.mockReset 行为 / 钩子并行→串行 / web components shadow root），本项目的 `useChat.test.js` 全不沾边。唯一沾边的是"钩子并行→串行"——但本测试只有 1 个文件 10 个用例，串行和并行无差异。
3. **vite 8 Rolldown 统一打包器**：vite 8 用 Rolldown 替代 Rollup，但 `defineConfig` / `plugins` / `server` / `build` 配置 API 不变。本项目的 `vite.config.js` 只有最基础的 proxy + outDir 配置，零适配成本。
4. **package-lock.json.bak 回滚预案**：升级前 `cp package-lock.json package-lock.json.bak`。最终无需回滚，但跨 major 升级前备份 lock 文件是值得的习惯——npm install 改了 34 个包，手动 undo 不现实。