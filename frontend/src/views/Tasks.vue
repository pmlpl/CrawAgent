<template>
  <div class="tasks-page">
    <header class="page-header">
      <h2 class="font-display">任务历史</h2>
      <button class="ghost-btn" @click="loadSessions">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <path d="M23 4v6h-6M1 20v-6h6"/>
          <path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/>
        </svg>
        刷新
      </button>
    </header>

    <div class="tasks-content">
      <!-- 加载中 -->
      <div class="loading-state" v-if="loading">
        <div class="typing-dots"><span></span><span></span><span></span></div>
        <p>加载中…</p>
      </div>

      <!-- 空状态 -->
      <div class="empty-state" v-else-if="sessions.length === 0">
        <svg width="48" height="48" viewBox="0 0 48 48" fill="none">
          <rect x="8" y="10" width="32" height="28" rx="3" stroke="var(--ink-faint)" stroke-width="2"/>
          <path d="M16 20h16M16 26h16M16 32h8" stroke="var(--ink-faint)" stroke-width="2" stroke-linecap="round"/>
        </svg>
        <p class="empty-title">还没有任务</p>
        <p class="empty-desc">去对话页面发一条消息，任务就会出现在这里</p>
      </div>

      <!-- 会话列表 -->
      <div class="session-list" v-else>
        <div
          v-for="s in sessions"
          :key="s.session_id"
          class="session-card fade-in-up"
        >
          <div class="session-info">
            <div class="session-name">{{ s.name || '未命名对话' }}</div>
            <div class="session-meta">
              <span class="meta-item">#{{ (s.id || s.session_id || '').slice(0, 12) }}</span>
              <span class="meta-item">{{ formatTime(s.created_at) }}</span>
              <span class="meta-item" v-if="s.lane_count">{{ s.lane_count }} 个 lane</span>
            </div>
          </div>
          <div class="session-actions">
            <button class="icon-btn" @click="goChat(s.session_id)" title="打开对话">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z"/>
              </svg>
            </button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { harnessApi } from '../api'

const router = useRouter()
const sessions = ref([])
const loading = ref(false)

onMounted(() => loadSessions())

async function loadSessions() {
  loading.value = true
  try {
    const res = await harnessApi.listSessions(50)
    sessions.value = res.sessions || res || []
  } catch (e) {
    sessions.value = []
  } finally {
    loading.value = false
  }
}

function goChat(sessionId) {
  router.push({ path: '/chat', query: { session_id: sessionId } })
}

function formatTime(ts) {
  if (!ts) return ''
  try {
    // 后端 created_at 为 epoch 秒，转毫秒后再解析
    const t = typeof ts === 'number' && ts < 1e12 ? ts * 1000 : ts
    const d = new Date(t)
    return `${d.getMonth() + 1}/${d.getDate()} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
  } catch {
    return ''
  }
}
</script>

<style scoped>
.tasks-page {
  display: flex;
  flex-direction: column;
  height: 100%;
  background: var(--paper);
}

.page-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 16px 28px;
  border-bottom: 1px solid var(--line);
  flex-shrink: 0;
}

.page-header h2 {
  font-size: 18px;
  color: var(--ink);
}

.ghost-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 6px 14px;
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
  background: var(--white);
  color: var(--ink-soft);
  font-size: 13px;
  cursor: pointer;
  transition: all 0.2s var(--ease);
}

.ghost-btn:hover {
  border-color: var(--terracotta);
  color: var(--terracotta);
}

.tasks-content {
  flex: 1;
  overflow-y: auto;
  padding: 24px 28px;
}

.session-list {
  max-width: 760px;
  margin: 0 auto;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.session-card {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 16px 20px;
  background: var(--white);
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
  transition: all 0.2s var(--ease);
}

.session-card:hover {
  border-color: var(--terracotta-soft);
  box-shadow: var(--shadow-sm);
}

.session-name {
  font-size: 14px;
  font-weight: 600;
  color: var(--ink);
  margin-bottom: 4px;
}

.session-meta {
  display: flex;
  gap: 12px;
  font-size: 12px;
  color: var(--ink-faint);
}

.meta-item {
  font-family: monospace;
}

.icon-btn {
  width: 32px;
  height: 32px;
  border: none;
  border-radius: 50%;
  background: var(--paper-warm);
  color: var(--ink-soft);
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  transition: all 0.2s var(--ease);
}

.icon-btn:hover {
  background: var(--terracotta);
  color: var(--white);
}

.loading-state, .empty-state {
  text-align: center;
  padding: 60px 20px;
  color: var(--ink-faint);
}

.empty-title {
  font-size: 16px;
  color: var(--ink-soft);
  margin: 16px 0 4px;
}

.empty-desc {
  font-size: 13px;
}
</style>
