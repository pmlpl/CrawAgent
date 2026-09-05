<script setup>
// CrawAgent WebUI — 应用外壳（经典三栏布局：常驻 Sidebar + Header + Content）
// 参考 crawagent-pro 的布局模式，但保留原 Vue + 丝绸主题设计
import { computed, provide, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import AppSidebar from './components/AppSidebar.vue'
import AppHeader from './components/AppHeader.vue'
import { useChat } from './composables/useChat'
import { useTheme } from './composables/useTheme'
import { useSettings } from './composables/useSettings'

const chat = useChat()
const { theme } = useTheme()
const settings = useSettings()
const route = useRoute()
const router = useRouter()

// 侧边栏开合状态：桌面端默认常驻打开，移动端默认关闭
const sidebarOpen = ref(window.innerWidth > 860)
provide('sidebarOpen', sidebarOpen)
provide('toggleSidebar', () => { sidebarOpen.value = !sidebarOpen.value })

// 当前页面类型
const isChatPage = computed(() => route.name === 'chat')
const isSettingsPage = computed(() => route.name === 'settings')
const isSitesPage = computed(() => route.name === 'sites' || route.name === 'site-detail')

// 当前页面标题（Header 显示）
const pageTitle = computed(() => {
  if (isSettingsPage.value) return '设置'
  if (route.name === 'site-detail') return '站点详情'
  if (isSitesPage.value) return '站点档案'
  // 聊天页：取会话第一条用户消息作为标题，否则显示默认
  const firstUser = chat.items?.find?.(m => m.kind === 'user')
  if (firstUser?.content) {
    const text = String(firstUser.content).replace(/\s+/g, ' ').trim()
    return text.slice(0, 30) + (text.length > 30 ? '…' : '')
  }
  return 'CrawAgent · 吐丝结网'
})

// 路由跳转
function navigate(path) {
  router.push(path)
}

// 新会话
function handleNewChat() {
  chat.newSession()
  if (route.name !== 'chat') navigate('/chat')
}

// 切换会话
function handleSelectSession(id) {
  chat.switchSession(id)
  if (route.name !== 'chat') navigate('/chat')
}

// 彻底删除会话（不归档）
function handleDeleteSession(id) {
  chat.deleteSession(id)
}

// 归档会话（导出到 logs/ 后移除）
function handleArchiveSession(id) {
  chat.archiveSession(id)
}

// 批量删除会话
function handleBulkDelete(ids) {
  chat.batchDeleteSessions(ids)
}

// 重命名会话
function handleRenameSession(id, title) {
  chat.renameSession(id, title)
}
</script>

<template>
  <div class="app-shell" :class="theme">
    <!-- 左侧：常驻侧边栏（桌面端固定，移动端抽屉） -->
    <AppSidebar
      :open="sidebarOpen"
      :sessions="chat.sessions.value"
      :active-id="chat.session.value"
      :is-settings-page="isSettingsPage"
      :is-sites-page="isSitesPage"
      @new-chat="handleNewChat"
      @select-session="handleSelectSession"
      @delete-session="handleDeleteSession"
      @archive-session="handleArchiveSession"
      @bulk-delete="handleBulkDelete"
      @rename-session="handleRenameSession"
      @open-settings="navigate('/settings')"
      @open-sites="navigate('/sites')"
      @close="sidebarOpen = false"
    />

    <!-- 右侧：主内容区 -->
    <div class="main-col">
      <!-- 顶部 Header -->
      <AppHeader
        :sidebar-open="sidebarOpen"
        :is-chat-page="isChatPage"
        :is-settings-page="isSettingsPage"
        :is-sites-page="isSitesPage"
        :title="pageTitle"
        :session-id="chat.session.value"
        :default-model="settings.config.defaultModel"
        @toggle-sidebar="sidebarOpen = !sidebarOpen"
        @new-session="handleNewChat"
      />

      <!-- 页面内容出口 -->
      <main class="page-content">
        <router-view />
      </main>

    </div>
  </div>
</template>

<style scoped>
.app-shell {
  width: 100vw;
  height: 100vh;
  display: flex;
  align-items: stretch;
  overflow: hidden;
  background: var(--bg);
  color: var(--ink);
}

.main-col {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  min-height: 0;
}

.page-content {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

</style>
