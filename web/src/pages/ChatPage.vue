<script setup>
import { nextTick, onMounted, ref, watch } from 'vue'
import { marked } from 'marked'
import DOMPurify from 'dompurify'
import EmptyState from '../components/EmptyState.vue'
import CrawlTrace from '../components/CrawlTrace.vue'
import Thinking from '../components/Thinking.vue'
import ChatComposer from '../components/ChatComposer.vue'
import { useChat } from '../composables/useChat'

const chat = useChat()

marked.setOptions({ gfm: true, breaks: true })
const _mdCache = new Map()
const MD_CACHE_MAX = 200
function md(text) {
  if (!text) return ''
  const cached = _mdCache.get(text)
  if (cached !== undefined) return cached
  const html = DOMPurify.sanitize(marked.parse(text))
  _mdCache.set(text, html)
  // 简单淘汰：超过上限删最早插入的
  if (_mdCache.size > MD_CACHE_MAX) {
    const firstKey = _mdCache.keys().next().value
    _mdCache.delete(firstKey)
  }
  return html
}
const composer = ref(null)
const restoreDraft = ref('')
const streamRef = ref(null)
const prevTyping = ref(false)

// 消息变化时滚动：新 trace 滚到 trace，其他滚到底部
watch(
  () => chat.items.length,
  () => {
    const lastItem = chat.items[chat.items.length - 1]
    if (lastItem && lastItem.kind === 'trace') {
      scrollToTrace()
    } else {
      scrollDown()
    }
  }
)
watch(chat.items, () => {
  const lastItem = chat.items[chat.items.length - 1]
  if (lastItem && lastItem.kind === 'trace') {
    scrollToTrace()
  } else {
    scrollDown()
  }
}, { deep: true })

// 任务完成时（typing 从 true → false），滚动显示最后一个 trace
watch(chat.typing, (val, old) => {
  if (old && !val) {
    scrollToTrace()
  }
})

function scrollDown() {
  nextTick(() => {
    const el = streamRef.value
    if (el) el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' })
  })
}

function scrollToTrace() {
  nextTick(() => {
    const el = streamRef.value
    if (!el) return
    const traces = el.querySelectorAll('.trace')
    if (traces.length > 0) {
      const lastTrace = traces[traces.length - 1]
      const rect = lastTrace.getBoundingClientRect()
      const containerRect = el.getBoundingClientRect()
      const offset = rect.top - containerRect.top + el.scrollTop - 20
      el.scrollTo({ top: Math.max(0, offset), behavior: 'smooth' })
    } else {
      scrollDown()
    }
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

// useChat 为单例：路由切走再回来时 items 仍在，避免重复 loadHistory 导致消息翻倍
onMounted(() => {
  if (!chat.connected.value) chat.connect()
  if (!chat.items.length) {
    chat.loadHistory()
    // fetchSessions 已在 main.js 启动时调用
  }
})
</script>

<template>
  <div class="chat-page">
    <!-- 连接断开提示横幅 -->
    <div v-if="!chat.connected.value" class="banner" role="button" tabindex="0" @click="chat.reconnect" @keydown.enter="chat.reconnect">
      连接已断开 · <b>点击重新连线</b>
    </div>

    <!-- 消息流 -->
    <main class="stream" ref="streamRef">
      <EmptyState v-if="!chat.items.length" @suggest="onSuggest" />

      <template v-for="item in chat.items" :key="item._id || item.kind + '_' + (item.content?.substring?.(0,20) || '')">
        <div v-if="item.kind === 'user'" class="msg user">{{ item.content }}</div>
        <div v-if="item.kind === 'ai'" class="msg ai md" :class="{ streaming: item.streaming }" v-html="md(item.content)"></div>
        <CrawlTrace v-if="item.kind === 'trace'" :steps="item.steps" :expanded="item._expanded" :round-id="item.roundId" />
        <Thinking v-if="item.kind === 'thinking'" :content="item.content" />
        <div v-if="item.kind === 'error'" class="err">{{ item.content }}</div>
      </template>

      <div v-if="chat.typing.value" class="typing">
        <span class="dig-scene" aria-hidden="true">
          <!-- 跳动粒子：5 颗像素点错落弹跳 -->
          <i class="dot" style="--i:0" /><i class="dot" style="--i:1" /><i class="dot" style="--i:2" /><i class="dot" style="--i:3" /><i class="dot" style="--i:4" />
        </span>
        <span class="typing-label">
          <span class="dig-brand">dig deep</span>
          <template v-if="chat.currentProgress.value">
            <span class="dig-sep">·</span>
            <span class="dig-progress">{{ chat.currentProgress.value }}</span>
          </template>
        </span>
      </div>
    </main>

    <!-- 输入组件：包含任务进度、工具栏、发送按钮 -->
    <ChatComposer
      ref="composer"
      :busy="chat.busy.value"
      :restore="restoreDraft"
      @send="onSend"
      @stop="chat.stop"
    />
  </div>
</template>

<style scoped>
.chat-page {
  width: 100%;
  height: 100%;
  display: flex;
  flex-direction: column;
  min-height: 0;
  overflow: hidden;
}

.stream {
  flex: 1;
  overflow-y: auto;
  overflow-x: hidden;
  width: 100%;
  max-width: var(--maxw);
  margin: 0 auto;
  padding: 28px 20px 12px;
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
  margin: 10px auto 0;
  max-width: var(--maxw);
  font-size: 13.5px;
  cursor: pointer;
}
.banner b { color: var(--accent); }

.typing {
  align-self: flex-start;
  display: inline-flex;
  align-items: center;
  gap: 12px;
  color: var(--accent);
  font-size: 13px;
  font-family: var(--font-mono);
  padding: 6px 14px;
  border-radius: 999px;
  background: color-mix(in srgb, var(--accent-soft) 55%, transparent);
  border: 1px solid color-mix(in srgb, var(--accent) 28%, transparent);
  animation: rise .35s var(--ease) both;
}
/* ===== 跳动粒子加载 ===== */
.dig-scene {
  display: inline-flex;
  align-items: flex-end;
  gap: 3px;
  width: 26px;
  height: 16px;
  flex: none;
}
.dig-scene .dot {
  width: 3.5px;
  height: 3.5px;
  border-radius: 1px;
  background: var(--accent);
  box-shadow: 0 0 5px color-mix(in srgb, var(--accent) 45%, transparent);
  animation: dot-bounce 1.15s ease-in-out infinite;
  animation-delay: calc(var(--i) * .12s);
}
@keyframes dot-bounce {
  0%, 100% { transform: translateY(0); opacity: .5; }
  35%      { transform: translateY(-9px); opacity: 1; }
  70%      { transform: translateY(0); }
}

/* ===== typing 文字标签 ===== */
.typing-label {
  color: var(--accent);
  letter-spacing: .04em;
  display: inline-flex;
  align-items: baseline;
  gap: 6px;
  max-width: 60vw;
  overflow: hidden;
}
.dig-brand {
  font-weight: 700;
  letter-spacing: .08em;
  text-transform: lowercase;
  flex: none;
}
.dig-sep {
  opacity: .5;
  flex: none;
}
.dig-progress {
  color: var(--ink);
  font-size: 12.5px;
  opacity: .85;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  animation: rise .3s var(--ease) both;
}
@keyframes pulse {
  0%, 100% { opacity: .4; }
  50% { opacity: 1; }
}
@keyframes rise {
  from { opacity: 0; transform: translateY(6px); }
  to { opacity: 1; transform: translateY(0); }
}

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
  .stream { padding: 20px 14px 12px; }
  .msg { max-width: 94%; }
}
</style>
