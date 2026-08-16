<script setup>
import { useTheme } from '../composables/useTheme'

defineProps({
  sessionId: { type: String, required: true },
})
const emit = defineEmits(['new-session', 'toggle-sidebar', 'open-settings'])

const { theme, toggle } = useTheme()
</script>

<template>
  <header class="topbar">
    <button class="btn btn-icon menu-btn" type="button" aria-label="打开会话列表" @click="emit('toggle-sidebar')">
      <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" aria-hidden="true"><path d="M4 7h16M4 12h16M4 17h16" /></svg>
    </button>

    <a class="brand" href="/">
      <svg width="34" height="34" viewBox="0 0 48 48" fill="none" aria-hidden="true">
        <g stroke="var(--accent)" stroke-width="1.6" opacity=".9">
          <path d="M24 4v40M4 24h40M10 10l28 28M38 10L10 38" />
          <circle cx="24" cy="24" r="8.5" />
          <circle cx="24" cy="24" r="16" />
        </g>
        <circle cx="24" cy="24" r="3.4" fill="var(--accent)" />
      </svg>
      <span>
        <span class="brand-name">CrawAgent</span>
        <span class="brand-tag">吐丝 · 结网 · 捕获信息</span>
      </span>
    </a>

    <span class="session-chip" title="当前会话 ID（与 CLI 的 session_id 互通）">
      session · <b>{{ sessionId }}</b>
    </span>

    <button class="btn" type="button" @click="emit('new-session')">
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" aria-hidden="true"><path d="M12 5v14M5 12h14" /></svg>
      新会话
    </button>

    <button class="btn btn-icon" type="button" aria-label="设置" @click="emit('open-settings')">
      <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="3" /><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09a1.65 1.65 0 0 0-1-1.51 1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09a1.65 1.65 0 0 0 1.51-1 1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33h0a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51h0a1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82v0a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" /></svg>
    </button>

    <button class="btn btn-icon" type="button" aria-label="切换明暗主题" @click="toggle">
      <svg v-if="theme === 'dark'" width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z" /></svg>
      <svg v-else width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><circle cx="12" cy="12" r="4" /><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" /></svg>
    </button>
  </header>
</template>

<style scoped>
.topbar {
  flex: none;
  z-index: 10;
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
  padding: 14px 20px;
  border-bottom: 1px solid var(--line);
  background: var(--bg);
  backdrop-filter: blur(14px);
  -webkit-backdrop-filter: blur(14px);
}

.brand {
  display: flex;
  align-items: center;
  gap: 11px;
  margin-right: auto;
  text-decoration: none;
  color: var(--ink);
}
.brand svg { display: block; }
.brand-name {
  font-family: var(--font-display);
  font-weight: 700;
  font-size: 21px;
  letter-spacing: .01em;
}
.brand-tag {
  font-size: 12.5px;
  color: var(--faint);
  letter-spacing: .14em;
  display: block;
  margin-top: -3px;
}

.session-chip {
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--dim);
  border: 1px dashed var(--line);
  border-radius: 999px;
  padding: 5px 12px;
  white-space: nowrap;
  cursor: default;
}
.session-chip b { color: var(--accent); font-weight: 600; }

/* 汉堡按钮仅移动端显示（桌面端侧栏常驻，无需开关） */
.menu-btn { display: none; }

@media (max-width: 860px) {
  .menu-btn { display: inline-flex; }
}

@media (max-width: 640px) {
  .topbar { padding: 12px 14px; gap: 8px; }
  .brand-tag { display: none; }
  .session-chip { order: 4; flex-basis: 100%; text-align: center; }
}
</style>
