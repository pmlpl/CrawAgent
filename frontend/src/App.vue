<template>
  <div class="app-shell">
    <!-- 侧边栏 -->
    <aside class="sidebar">
      <div class="sidebar-header">
        <div class="logo-mark">
          <svg width="28" height="28" viewBox="0 0 28 28" fill="none">
            <circle cx="14" cy="14" r="12" stroke="currentColor" stroke-width="1.5"/>
            <circle cx="14" cy="14" r="5" fill="currentColor"/>
            <circle cx="14" cy="14" r="2" fill="var(--paper)"/>
          </svg>
        </div>
        <div>
          <h1 class="logo-text font-display">CrawAgent</h1>
          <p class="logo-sub">爬虫智能助手</p>
        </div>
      </div>

      <nav class="nav-list">
        <router-link to="/chat" class="nav-item" active-class="nav-item--active">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z"/>
          </svg>
          <span>对话</span>
        </router-link>
        <router-link to="/tasks" class="nav-item" active-class="nav-item--active">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M9 11l3 3L22 4"/><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/>
          </svg>
          <span>任务</span>
        </router-link>
        <router-link to="/settings" class="nav-item" active-class="nav-item--active">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <circle cx="12" cy="12" r="3"/>
            <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/>
          </svg>
          <span>设置</span>
        </router-link>
        <router-link to="/output" class="nav-item" active-class="nav-item--active">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/>
          </svg>
          <span>文件</span>
        </router-link>
        <router-link to="/monitor" class="nav-item" active-class="nav-item--active">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M3 3v18h18"/><path d="M18 17V9"/><path d="M13 17V5"/><path d="M8 17v-3"/>
          </svg>
          <span>监控</span>
        </router-link>
        <router-link to="/security" class="nav-item" active-class="nav-item--active">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>
          </svg>
          <span>安全</span>
        </router-link>
        <router-link to="/video" class="nav-item" active-class="nav-item--active">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <polygon points="23 7 16 12 23 17 23 7"/>
            <rect x="1" y="5" width="15" height="14" rx="2" ry="2"/>
          </svg>
          <span>视频</span>
        </router-link>
      </nav>

      <div class="sidebar-footer">
        <div class="status-pill" :class="{ 'status-pill--live': serverOnline }">
          <span class="status-dot"></span>
          <span>{{ serverOnline ? '服务在线' : '服务离线' }}</span>
        </div>
      </div>
    </aside>

    <!-- 主内容区 -->
    <main class="main-area">
      <router-view v-slot="{ Component }">
        <keep-alive>
          <component :is="Component" />
        </keep-alive>
      </router-view>
    </main>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { systemApi } from './api'

const serverOnline = ref(false)

async function checkHealth() {
  try {
    await systemApi.health()
    serverOnline.value = true
  } catch {
    serverOnline.value = false
  }
}

onMounted(() => {
  checkHealth()
  setInterval(checkHealth, 30000)
})
</script>

<style scoped>
.app-shell {
  display: flex;
  height: 100vh;
  overflow: hidden;
}

/* 侧边栏 */
.sidebar {
  width: 220px;
  flex-shrink: 0;
  background: var(--paper-warm);
  border-right: 1px solid var(--line);
  display: flex;
  flex-direction: column;
  padding: 20px 16px;
}

.sidebar-header {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 4px 20px;
  border-bottom: 1px solid var(--line);
  margin-bottom: 16px;
}

.logo-mark {
  color: var(--terracotta);
  display: flex;
  align-items: center;
}

.logo-text {
  font-size: 18px;
  color: var(--ink);
  line-height: 1.2;
}

.logo-sub {
  font-size: 11px;
  color: var(--ink-faint);
  margin-top: 2px;
}

.nav-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
  flex: 1;
}

.nav-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 12px;
  border-radius: var(--radius-md);
  color: var(--ink-soft);
  text-decoration: none;
  font-size: 14px;
  font-weight: 500;
  transition: all 0.2s var(--ease);
}

.nav-item:hover {
  background: var(--white);
  color: var(--ink);
}

.nav-item--active {
  background: var(--white);
  color: var(--terracotta);
  box-shadow: var(--shadow-xs);
}

.sidebar-footer {
  padding-top: 12px;
  border-top: 1px solid var(--line);
}

.status-pill {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 6px 10px;
  border-radius: 20px;
  font-size: 12px;
  color: var(--ink-faint);
  background: var(--white);
}

.status-pill--live {
  color: var(--moss);
}

.status-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--ink-faint);
}

.status-pill--live .status-dot {
  background: var(--moss);
  box-shadow: 0 0 0 3px var(--moss-soft);
}

/* 主内容 */
.main-area {
  flex: 1;
  overflow: hidden;
  display: flex;
  flex-direction: column;
}

/* 响应式 */
@media (max-width: 768px) {
  .sidebar {
    width: 64px;
    padding: 16px 8px;
  }
  .logo-text, .logo-sub, .nav-item span, .status-pill span:last-child {
    display: none;
  }
  .nav-item {
    justify-content: center;
    padding: 12px;
  }
}
</style>
