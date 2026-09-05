<script setup>
// 顶部 Header（参考 crawagent-pro Header 设计 + 丝绸主题）
// - 左侧：菜单按钮 + 标题 + 模型标签
// - 右侧：导出 + 主题切换（新建会话入口在侧栏，避免重复按钮）
import { inject } from 'vue'
import { useTheme } from '../composables/useTheme'
import ContextRing from './ContextRing.vue'

const props = defineProps({
  sidebarOpen: { type: Boolean, default: true },
  isChatPage: { type: Boolean, default: false },
  isSettingsPage: { type: Boolean, default: false },
  isSitesPage: { type: Boolean, default: false },
  title: { type: String, default: '' },
  sessionId: { type: String, default: '' },
  defaultModel: { type: String, default: '' },
})
const emit = defineEmits(['toggle-sidebar'])

const { theme, toggle } = useTheme()
const toggleSidebar = inject('toggleSidebar', () => emit('toggle-sidebar'))

function exportUrl(format) {
  if (!props.sessionId) return ''
  return `/api/sessions/${encodeURIComponent(props.sessionId)}/export?format=${format}`
}

// 简化模型名称显示
function fmtModel(id) {
  if (!id) return ''
  return id
    .replace(/^(deepseek|gpt|claude|gemini|kimi|qwen|glm|minimax)[-_]*/i, '')
    .replace(/[-_]/g, ' ')
    .trim() || id
}
</script>

<template>
  <header class="header">
    <!-- 左侧：菜单按钮 + 标题 + 模型 -->
    <div class="left">
      <!-- 菜单按钮：移动端常显；桌面端仅侧栏收起时显示（重开入口） -->
      <button
        type="button"
        class="icon-btn menu-btn"
        :class="{ 'force-show': !sidebarOpen }"
        :aria-label="sidebarOpen ? '关闭侧边栏' : '打开侧边栏'"
        @click="toggleSidebar"
      >
        <svg v-if="sidebarOpen" width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" aria-hidden="true">
          <path d="M19 12H5M12 19l-7-7 7-7" />
        </svg>
        <svg v-else width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" aria-hidden="true">
          <path d="M4 7h16M4 12h16M4 17h16" />
        </svg>
      </button>

      <!-- 页面标题 -->
      <h1 class="title">{{ title }}</h1>

      <!-- 聊天页：显示会话 ID + 模型标签 -->
      <span v-if="isChatPage && sessionId" class="session-chip" title="当前会话 ID">
        <span class="chip-label">session</span>
        <b>{{ sessionId }}</b>
      </span>
      <span v-if="isChatPage && defaultModel" class="model-chip" :title="defaultModel">
        <span class="chip-label">model</span>
        <b>{{ fmtModel(defaultModel) }}</b>
      </span>

      <!-- 上下文余量环：聊天页显示 -->
      <ContextRing v-if="isChatPage" :session-id="sessionId" />
    </div>

    <!-- 右侧：操作按钮 -->
    <div class="right">
      <!-- 聊天页：导出会话（JSON / Markdown） -->
      <details v-if="isChatPage && sessionId" class="export-dropdown">
        <summary class="icon-btn export-btn" title="导出会话" aria-label="导出会话">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
            <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
            <polyline points="7 10 12 15 17 10" />
            <line x1="12" y1="15" x2="12" y2="3" />
          </svg>
        </summary>
        <div class="export-menu">
          <a :href="exportUrl('json')" download title="完整数据，机器可读">JSON</a>
          <a :href="exportUrl('md')" download title="对话纪要，人类可读">Markdown</a>
        </div>
      </details>

      <!-- 主题切换 -->
      <button
        type="button"
        class="icon-btn theme-btn"
        :aria-label="theme === 'dark' ? '切换到浅色模式' : '切换到深色模式'"
        :title="theme === 'dark' ? '切换到浅色模式' : '切换到深色模式'"
        @click="toggle"
      >
        <svg v-if="theme === 'dark'" width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          <path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z" />
        </svg>
        <svg v-else width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          <circle cx="12" cy="12" r="4" />
          <path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
        </svg>
      </button>
    </div>
  </header>
</template>

<style scoped>
.header {
  flex: none;
  z-index: 10;
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 20px;
  border-bottom: 1px solid var(--line);
  background: var(--bg);
  backdrop-filter: blur(14px);
  -webkit-backdrop-filter: blur(14px);
}

.left {
  flex: 1;
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 10px;
}
.right {
  flex: none;
  display: flex;
  align-items: center;
  gap: 8px;
}

/* ---------- 图标按钮 ---------- */
.icon-btn {
  appearance: none;
  border: 1px solid var(--line);
  background: var(--panel);
  color: var(--dim);
  width: 36px;
  height: 36px;
  border-radius: 10px;
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  transition: background .15s, color .15s, border-color .15s;
}
.icon-btn:hover {
  background: var(--hover);
  color: var(--accent);
  border-color: color-mix(in srgb, var(--accent) 40%, transparent);
}

/* 菜单按钮：移动端常显；桌面端仅侧栏收起时显示（重开入口） */
.menu-btn { display: none; }
.menu-btn.force-show { display: inline-flex; }
@media (max-width: 860px) {
  .menu-btn { display: inline-flex; }
}

/* ---------- 标题 ---------- */
.title {
  font-size: 15px;
  font-weight: 600;
  color: var(--ink);
  margin: 0;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 40vw;
}

/* ---------- Chip（会话 ID + 模型） ---------- */
.session-chip,
.model-chip {
  flex: none;
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 4px 10px;
  border-radius: 999px;
  font-family: var(--font-mono);
  font-size: 11.5px;
  line-height: 1.4;
  cursor: default;
}
.session-chip {
  border: 1px dashed var(--line);
  color: var(--dim);
}
.session-chip b {
  color: var(--accent);
  font-weight: 600;
}
.model-chip {
  border: 1px solid color-mix(in srgb, var(--accent) 40%, transparent);
  background: var(--accent-soft);
  color: var(--dim);
}
.model-chip b {
  color: var(--accent);
  font-weight: 600;
}
.chip-label {
  font-size: 10px;
  letter-spacing: .1em;
  text-transform: uppercase;
  opacity: .65;
}

/* ---------- 导出下拉 ---------- */
.export-dropdown {
  position: relative;
  flex: none;
}
.export-dropdown > summary {
  list-style: none;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
}
.export-dropdown > summary::-webkit-details-marker { display: none; }
.export-dropdown[open] > summary {
  background: var(--hover);
  color: var(--accent);
  border-color: color-mix(in srgb, var(--accent) 40%, transparent);
}
.export-menu {
  position: absolute;
  top: calc(100% + 6px);
  right: 0;
  min-width: 150px;
  border: 1px solid var(--line);
  border-radius: 10px;
  background: var(--panel);
  box-shadow: var(--shadow);
  padding: 4px;
  z-index: 50;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.export-menu a {
  display: block;
  padding: 8px 12px;
  border-radius: 7px;
  font-size: 13px;
  color: var(--dim);
  text-decoration: none;
  transition: background .12s, color .12s;
}
.export-menu a:hover {
  background: var(--hover);
  color: var(--accent);
}

/* 移动端优化 */
@media (max-width: 720px) {
  .header { padding: 10px 14px; gap: 8px; }
  .session-chip { display: none; }
  .title { max-width: 50vw; font-size: 14px; }
  .model-chip .chip-label { display: none; }
}

/* ---------- 导出下拉 ---------- */
</style>
