<script setup>
// CrawAgent WebUI — 主组件：消息流渲染 + 自动滚动 + 失败草稿恢复
import { nextTick, onMounted, ref, watch } from 'vue'
import { marked } from 'marked'
import DOMPurify from 'dompurify'
import TopBar from './components/TopBar.vue'
import EmptyState from './components/EmptyState.vue'
import CrawlTrace from './components/CrawlTrace.vue'
import Thinking from './components/Thinking.vue'
import ChatComposer from './components/ChatComposer.vue'
import SessionSidebar from './components/SessionSidebar.vue'
import SettingsModal from './components/SettingsModal.vue'
import { useChat } from './composables/useChat'
import { useSettings } from './composables/useSettings'

const chat = useChat()
const settings = useSettings()

// AI 回复渲染为 Markdown（gfm + 换行转 <br>），经 DOMPurify 清洗防 XSS
marked.setOptions({ gfm: true, breaks: true })
function md(text) {
  if (!text) return ''
  return DOMPurify.sanitize(marked.parse(text))
}
const composer = ref(null)
const restoreDraft = ref('')
const sidebarOpen = ref(false)
const streamRef = ref(null)

// 消息流变化（含轨迹步骤的深层变更）后滚到底部
watch(
  () => [chat.items.length, chat.typing.value],
  scrollDown,
)
watch(chat.items, () => scrollDown(), { deep: true })

function scrollDown() {
  nextTick(() => {
    const el = streamRef.value
    if (el) el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' })
  })
}

// 新增 error 条目时，把草稿还给输入框（不清空用户输入）
watch(
  () => chat.items.filter(i => i.kind === 'error').length,
  (n, old) => {
    if (n > old && chat.lastDraft.value) {
      restoreDraft.value = ''
      nextTick(() => { restoreDraft.value = chat.lastDraft.value })
    }
  },
)

function onSend(text) {
  chat.send(text)
}

function onSuggest(text) {
  composer.value?.fill(text)
}

function onNewSession() {
  chat.newSession()
  sidebarOpen.value = false
  nextTick(() => composer.value?.fill(''))
}

function onSelectSession(id) {
  chat.switchSession(id)
}

function onOpenSettings() {
  settings.open()
}

onMounted(() => {
  chat.connect()
  chat.loadHistory()
  chat.fetchSessions()
})
</script>

<template>
  <TopBar
    :session-id="chat.session.value"
    @new-session="onNewSession"
    @toggle-sidebar="sidebarOpen = !sidebarOpen"
    @open-settings="onOpenSettings"
  />

  <div class="layout">
    <SessionSidebar
      :sessions="chat.sessions.value"
      :active-id="chat.session.value"
      :open="sidebarOpen"
      @select="onSelectSession"
      @close="sidebarOpen = false"
    />

    <div class="chat-col">
      <main class="stream" ref="streamRef">
        <EmptyState v-if="!chat.items.length" @suggest="onSuggest" />

        <template v-for="(item, i) in chat.items" :key="i">
          <div v-if="item.kind === 'user'" class="msg user">{{ item.content }}</div>
          <div v-else-if="item.kind === 'ai'" class="msg ai md" :class="{ streaming: item.streaming }" v-html="md(item.content)"></div>
          <CrawlTrace v-else-if="item.kind === 'trace'" :steps="item.steps" />
          <Thinking v-else-if="item.kind === 'thinking'" :content="item.content" />
          <div v-else-if="item.kind === 'error'" class="err">{{ item.content }}</div>
        </template>

        <div v-if="chat.typing.value" class="typing">
          <i /><i /><i /><span>正在吐丝…</span>
        </div>
      </main>

      <div v-if="!chat.connected.value" class="banner" role="button" tabindex="0" @click="chat.reconnect" @keydown.enter="chat.reconnect">
        连接已断开 · <b>点击重新连线</b>
      </div>

      <ChatComposer
        ref="composer"
        :busy="chat.busy.value"
        :restore="restoreDraft"
        @send="onSend"
      />

      <div v-if="chat.lastStatus.value" class="statusbar" :title="chat.lastStatus.value">
        <span class="sb-dot" aria-hidden="true">◆</span>
        <span class="sb-text">{{ chat.lastStatus.value }}</span>
      </div>
    </div>
  </div>

  <SettingsModal />
</template>

<style scoped>
.layout {
  flex: 1;
  display: flex;
  align-items: stretch;
  min-height: 0;
  overflow: hidden;
}

.chat-col {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  min-height: 0;
}

.stream {
  flex: 1;
  overflow-y: auto;
  overflow-x: hidden;
  width: 100%;
  max-width: var(--maxw);
  margin: 0 auto;
  padding: 28px 20px 24px;
  display: flex;
  flex-direction: column;
  gap: 18px;
  scroll-behavior: smooth;
}

.msg {
  max-width: 82%;
  padding: 12px 16px;
  border-radius: var(--radius);
  white-space: pre-wrap;
  word-break: break-word;
  animation: rise .35s var(--ease) both;
}
.msg.user {
  align-self: flex-end;
  background: var(--accent-soft);
  border: 1px solid color-mix(in srgb, var(--accent) 35%, transparent);
  border-bottom-right-radius: 4px;
}
.msg.ai {
  align-self: flex-start;
  background: var(--panel);
  border: 1px solid var(--line);
  border-bottom-left-radius: 4px;
  box-shadow: var(--shadow);
}
/* Markdown 渲染（AI 回复）：v-html 注入，需用 :deep 命中内部元素 */
.msg.ai.md {
  white-space: normal;
  overflow-wrap: break-word;
}
.msg.ai.md :deep(p) { margin: 0 0 .6em; }
.msg.ai.md :deep(p:last-child) { margin-bottom: 0; }
.msg.ai.md :deep(h1), .msg.ai.md :deep(h2), .msg.ai.md :deep(h3), .msg.ai.md :deep(h4) {
  margin: 1em 0 .5em; line-height: 1.3; font-weight: 600;
}
.msg.ai.md :deep(h1) { font-size: 1.4em; }
.msg.ai.md :deep(h2) { font-size: 1.25em; }
.msg.ai.md :deep(h3) { font-size: 1.1em; }
.msg.ai.md :deep(h4) { font-size: 1em; }
.msg.ai.md :deep(ul), .msg.ai.md :deep(ol) { margin: .4em 0 .8em; padding-left: 1.4em; }
.msg.ai.md :deep(li) { margin: .25em 0; }
.msg.ai.md :deep(li)::marker { color: var(--accent); }
.msg.ai.md :deep(a) { color: var(--accent-strong); text-decoration: none; }
.msg.ai.md :deep(a:hover) { text-decoration: underline; }
.msg.ai.md :deep(code) {
  font-family: var(--font-mono); font-size: .88em;
  background: var(--panel-2); padding: .12em .4em; border-radius: 5px;
}
.msg.ai.md :deep(pre) {
  background: var(--panel-2); border: 1px solid var(--line); border-radius: 8px;
  padding: 12px 14px; overflow-x: auto; margin: .6em 0;
}
.msg.ai.md :deep(pre code) { background: none; padding: 0; font-size: .85em; }
.msg.ai.md :deep(blockquote) {
  margin: .6em 0; padding: .2em .9em; border-left: 3px solid var(--accent); color: var(--dim);
}
.msg.ai.md :deep(table) { border-collapse: collapse; margin: .6em 0; width: 100%; }
.msg.ai.md :deep(th), .msg.ai.md :deep(td) { border: 1px solid var(--line); padding: 6px 10px; text-align: left; }
.msg.ai.md :deep(th) { background: var(--panel-2); }
.msg.ai.md :deep(hr) { border: none; border-top: 1px solid var(--line); margin: .8em 0; }
.msg.ai.md :deep(img) { max-width: 100%; border-radius: 8px; }
/* 流式输出中的光标：一缕正在生长的丝 */
.msg.ai.streaming::after {
  content: "▍";
  color: var(--accent);
  animation: pulse 1s ease-in-out infinite;
}

/* 状态栏：常驻在 Composer 下方的小型 monospace 指标条 */
.statusbar {
  flex: none;
  max-width: var(--maxw);
  width: 100%;
  margin: 0 auto;
  padding: 6px 20px calc(10px + env(safe-area-inset-bottom));
  display: flex;
  align-items: center;
  gap: 9px;
  font-family: var(--font-mono);
  font-size: 11.5px;
  color: var(--faint);
  letter-spacing: .02em;
  overflow: hidden;
  white-space: nowrap;
  animation: rise .3s var(--ease) both;
}
.sb-dot {
  color: var(--accent);
  font-size: 9px;
  flex: none;
}
.sb-text {
  overflow: hidden;
  text-overflow: ellipsis;
  flex: 1;
  min-width: 0;
}

.err {
  align-self: stretch;
  border: 1px solid color-mix(in srgb, var(--danger) 45%, transparent);
  background: var(--danger-soft);
  color: var(--danger);
  border-radius: var(--radius);
  padding: 12px 16px;
  font-size: 14px;
  animation: rise .3s var(--ease) both;
}

.banner {
  flex: none;
  text-align: center;
  background: var(--panel-2);
  border: 1px solid var(--line);
  color: var(--dim);
  border-radius: 999px;
  padding: 8px 18px;
  margin: 0 auto 4px;
  max-width: var(--maxw);
  font-size: 13.5px;
  cursor: pointer;
}
.banner b { color: var(--accent); }

.typing {
  align-self: flex-start;
  display: inline-flex;
  align-items: center;
  gap: 9px;
  color: var(--faint);
  font-size: 13.5px;
  font-family: var(--font-mono);
  padding: 4px 2px;
}
.typing i {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--accent);
  animation: pulse 1.1s ease-in-out infinite;
}
.typing i:nth-child(2) { animation-delay: .18s; }
.typing i:nth-child(3) { animation-delay: .36s; }

/* 滚动条样式 */
.stream::-webkit-scrollbar { width: 7px; }
.stream::-webkit-scrollbar-track { background: transparent; }
.stream::-webkit-scrollbar-thumb {
  background: color-mix(in srgb, var(--silk) 60%, transparent);
  border-radius: 999px;
}
.stream::-webkit-scrollbar-thumb:hover {
  background: color-mix(in srgb, var(--accent) 40%, transparent);
}

@media (max-width: 640px) {
  .stream { padding: 20px 14px 16px; }
  .msg { max-width: 94%; }
}
</style>
