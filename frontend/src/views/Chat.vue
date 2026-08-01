<template>
  <div class="chat-page">
    <!-- 顶部栏 -->
    <header class="chat-header">
      <div class="header-left">
        <h2 class="font-display">{{ currentSessionName }}</h2>
        <span class="session-id" v-if="sessionId">#{{ sessionId.slice(0, 8) }}</span>
      </div>
      <div class="header-right">
        <button class="ghost-btn" @click="newSession" title="新建对话">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M12 5v14M5 12h14"/>
          </svg>
          新建
        </button>
      </div>
    </header>

    <!-- 消息区 -->
    <div class="messages-wrap" ref="messagesWrap">
      <div class="messages-list" v-if="messages.length > 0">
        <div
          v-for="(msg, i) in messages"
          :key="i"
          class="msg-row fade-in-up"
          :class="msg.role"
        >
          <div class="msg-avatar">
            <template v-if="msg.role === 'user'">
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/>
                <circle cx="12" cy="7" r="4"/>
              </svg>
            </template>
            <template v-else>
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <circle cx="12" cy="12" r="10"/>
                <circle cx="12" cy="12" r="4" fill="currentColor"/>
              </svg>
            </template>
          </div>
          <div class="msg-content">
            <div class="msg-role">{{ msg.role === 'user' ? '你' : '助手' }}</div>
            <div class="msg-text" v-html="renderContent(msg.content)"></div>
            <div class="msg-tools" v-if="msg.toolCalls && msg.toolCalls.length">
              <div class="tool-chip" v-for="(tc, j) in msg.toolCalls" :key="j">
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                  <path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/>
                </svg>
                {{ tc.name || tc.tool_name }}
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- 空状态：欢迎卡片 -->
      <div class="welcome-state" v-else>
        <div class="welcome-icon">
          <svg width="48" height="48" viewBox="0 0 48 48" fill="none">
            <circle cx="24" cy="24" r="20" stroke="var(--terracotta)" stroke-width="2" opacity="0.3"/>
            <circle cx="24" cy="24" r="12" fill="var(--terracotta)" opacity="0.15"/>
            <circle cx="24" cy="24" r="5" fill="var(--terracotta)"/>
          </svg>
        </div>
        <h2 class="font-display welcome-title">你好，我是 CrawAgent</h2>
        <p class="welcome-desc">告诉我你想爬取什么，我来帮你完成。不需要懂编程，用大白话说就行。</p>
        <div class="example-cards">
          <button class="example-card" @click="sendExample(ex.text)" v-for="ex in examples" :key="ex.text">
            <span class="example-emoji">{{ ex.icon }}</span>
            <span class="example-text">{{ ex.text }}</span>
          </button>
        </div>
      </div>

      <!-- 加载中 -->
      <div class="loading-row" v-if="loading">
        <div class="msg-avatar assistant">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <circle cx="12" cy="12" r="10"/>
            <circle cx="12" cy="12" r="4" fill="currentColor"/>
          </svg>
        </div>
        <div class="typing-dots"><span></span><span></span><span></span></div>
      </div>
    </div>

    <!-- 输入区 -->
    <div class="input-area">
      <div class="input-box">
        <textarea
          ref="inputRef"
          v-model="inputText"
          class="input-textarea"
          :placeholder="inputPlaceholder"
          rows="1"
          @keydown.enter.exact.prevent="send"
          @keydown.shift.enter="inputText += '\n'"
          @input="autoResize"
        ></textarea>
        <button class="send-btn" :disabled="!inputText.trim() || loading" @click="send">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M22 2L11 13M22 2l-7 20-4-9-9-4 20-7z"/>
          </svg>
        </button>
      </div>
      <p class="input-hint">按 Enter 发送 · Shift + Enter 换行</p>
    </div>
  </div>
</template>

<script setup>
import { ref, nextTick, onMounted } from 'vue'
import { harnessApi } from '../api'

const messages = ref([])
const inputText = ref('')
const loading = ref(false)
const sessionId = ref('')
const currentSessionName = ref('新对话')
const messagesWrap = ref(null)
const inputRef = ref(null)

const examples = [
  { icon: '📰', text: '爬取 Hacker News 首页标题' },
  { icon: '🛒', text: '监控某个商品价格变化' },
  { icon: '📚', text: '抓取一个小说网站的内容' },
  { icon: '🔍', text: '检查我的网站有没有安全漏洞' },
]

const inputPlaceholder = '想爬取什么？用大白话说就行…'

onMounted(async () => {
  // 尝试恢复已有会话
  try {
    const res = await harnessApi.listSessions(1)
    const list = res.sessions || []
    if (list.length > 0) {
      sessionId.value = list[0].id || list[0].session_id
      currentSessionName.value = list[0].name || '已有对话'
      await loadHistory()
      return
    }
  } catch (e) {
    console.warn('获取会话列表失败:', e)
  }
  // 没有会话则新建
  await newSession()
})

async function newSession() {
  try {
    const res = await harnessApi.createSession('新对话')
    sessionId.value = res.session_id
    currentSessionName.value = res.name || '新对话'
    messages.value = []
  } catch (e) {
    messages.value.push({
      role: 'assistant',
      content: '⚠️ 无法连接到服务器，请确认后端已启动（http://localhost:8000）',
    })
  }
}

async function loadHistory() {
  try {
    const res = await harnessApi.getHistory(sessionId.value, 50)
    const entries = res.entries || []
    if (entries.length > 0) {
      messages.value = entries.map(e => ({
        role: e.role,
        content: e.content || '',
        toolCalls: e.tool_calls || [],
      }))
    }
    await scrollToBottom()
  } catch (e) {
    console.warn('加载历史失败:', e)
  }
}

async function send() {
  const text = inputText.value.trim()
  if (!text || loading.value) return

  // 如果没有 session，先创建
  if (!sessionId.value) {
    await newSession()
  }

  messages.value.push({ role: 'user', content: text })
  inputText.value = ''
  await nextTick()
  autoResize()
  await scrollToBottom()

  loading.value = true
  try {
    const res = await harnessApi.prompt(sessionId.value, text)
    loading.value = false

    if (res.error) {
      messages.value.push({
        role: 'assistant',
        content: `⚠️ 出错了：${res.error}`,
      })
    } else if (res.kind === 'completed') {
      // 重新加载历史获取助手回复
      await loadHistory()
    } else {
      messages.value.push({
        role: 'assistant',
        content: `任务状态：${res.kind}`,
      })
    }
  } catch (e) {
    loading.value = false
    const errMsg = e.response?.data?.detail || e.message || '网络错误'
    messages.value.push({
      role: 'assistant',
      content: `⚠️ 请求失败：${errMsg}`,
    })
  }
  await scrollToBottom()
}

function sendExample(text) {
  inputText.value = text
  send()
}

async function scrollToBottom() {
  await nextTick()
  if (messagesWrap.value) {
    messagesWrap.value.scrollTop = messagesWrap.value.scrollHeight
  }
}

function autoResize() {
  const el = inputRef.value
  if (!el) return
  el.style.height = 'auto'
  el.style.height = Math.min(el.scrollHeight, 160) + 'px'
}

function renderContent(content) {
  if (!content) return ''
  // 简单转义 HTML
  let html = content
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
  // 代码块
  html = html.replace(/```(\w*)\n?([\s\S]*?)```/g, '<pre class="code-block"><code>$2</code></pre>')
  // 行内代码
  html = html.replace(/`([^`]+)`/g, '<code class="inline-code">$1</code>')
  // 换行
  html = html.replace(/\n/g, '<br>')
  return html
}
</script>

<style scoped>
.chat-page {
  display: flex;
  flex-direction: column;
  height: 100%;
  background: var(--paper);
}

/* 顶部栏 */
.chat-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 16px 28px;
  border-bottom: 1px solid var(--line);
  background: var(--paper);
  flex-shrink: 0;
}

.header-left {
  display: flex;
  align-items: baseline;
  gap: 8px;
}

.header-left h2 {
  font-size: 18px;
  color: var(--ink);
}

.session-id {
  font-size: 12px;
  color: var(--ink-faint);
  font-family: monospace;
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
  font-weight: 500;
  cursor: pointer;
  transition: all 0.2s var(--ease);
}

.ghost-btn:hover {
  border-color: var(--terracotta);
  color: var(--terracotta);
  background: var(--paper-warm);
}

/* 消息区 */
.messages-wrap {
  flex: 1;
  overflow-y: auto;
  padding: 24px 28px;
  scroll-behavior: smooth;
}

.messages-list {
  max-width: 760px;
  margin: 0 auto;
  display: flex;
  flex-direction: column;
  gap: 20px;
}

/* 消息行 */
.msg-row {
  display: flex;
  gap: 12px;
}

.msg-avatar {
  width: 32px;
  height: 32px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  background: var(--paper-warm);
  color: var(--ink-soft);
}

.msg-row.user .msg-avatar {
  background: var(--terracotta-soft);
  color: var(--terracotta);
}

.msg-row.assistant .msg-avatar {
  background: var(--terracotta);
  color: var(--white);
}

.msg-content {
  flex: 1;
  min-width: 0;
}

.msg-role {
  font-size: 12px;
  font-weight: 600;
  color: var(--ink-faint);
  margin-bottom: 4px;
}

.msg-text {
  font-size: 14px;
  line-height: 1.7;
  color: var(--ink);
  word-wrap: break-word;
}

.msg-tools {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 8px;
}

.tool-chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 3px 8px;
  background: var(--terracotta-soft);
  color: var(--terracotta-dark);
  border-radius: 6px;
  font-size: 11px;
  font-weight: 500;
}

/* 代码块 */
:deep(.code-block) {
  background: #1C1917;
  color: #F5F1EA;
  padding: 14px 16px;
  border-radius: var(--radius-md);
  overflow-x: auto;
  font-size: 13px;
  line-height: 1.5;
  margin: 8px 0;
}

:deep(.inline-code) {
  background: var(--paper-warm);
  padding: 2px 6px;
  border-radius: 4px;
  font-size: 13px;
  font-family: 'Courier New', monospace;
}

/* 加载行 */
.loading-row {
  display: flex;
  align-items: center;
  gap: 12px;
  max-width: 760px;
  margin: 0 auto;
  padding-top: 20px;
}

/* 欢迎状态 */
.welcome-state {
  max-width: 560px;
  margin: 8vh auto;
  text-align: center;
}

.welcome-icon {
  margin-bottom: 20px;
  display: flex;
  justify-content: center;
}

.welcome-title {
  font-size: 28px;
  color: var(--ink);
  margin-bottom: 8px;
}

.welcome-desc {
  font-size: 15px;
  color: var(--ink-soft);
  line-height: 1.6;
  margin-bottom: 32px;
}

.example-cards {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;
}

.example-card {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 14px 16px;
  background: var(--white);
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
  cursor: pointer;
  text-align: left;
  font-size: 13px;
  color: var(--ink-soft);
  transition: all 0.2s var(--ease);
}

.example-card:hover {
  border-color: var(--terracotta);
  color: var(--terracotta);
  background: var(--paper-warm);
  transform: translateY(-1px);
  box-shadow: var(--shadow-sm);
}

.example-emoji {
  font-size: 18px;
}

/* 输入区 */
.input-area {
  padding: 12px 28px 20px;
  background: var(--paper);
  flex-shrink: 0;
}

.input-box {
  max-width: 760px;
  margin: 0 auto;
  display: flex;
  align-items: flex-end;
  gap: 8px;
  background: var(--white);
  border: 1px solid var(--line);
  border-radius: var(--radius-lg);
  padding: 8px 8px 8px 16px;
  box-shadow: var(--shadow-sm);
  transition: border-color 0.2s var(--ease);
}

.input-box:focus-within {
  border-color: var(--terracotta);
  box-shadow: 0 0 0 3px var(--terracotta-soft);
}

.input-textarea {
  flex: 1;
  border: none;
  outline: none;
  resize: none;
  font-family: var(--font-body);
  font-size: 14px;
  line-height: 1.5;
  color: var(--ink);
  background: transparent;
  padding: 6px 0;
  max-height: 160px;
}

.input-textarea::placeholder {
  color: var(--ink-faint);
}

.send-btn {
  width: 36px;
  height: 36px;
  border: none;
  border-radius: 50%;
  background: var(--terracotta);
  color: var(--white);
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  flex-shrink: 0;
  transition: all 0.2s var(--ease);
}

.send-btn:hover:not(:disabled) {
  background: var(--terracotta-dark);
  transform: scale(1.05);
}

.send-btn:disabled {
  background: var(--ink-faint);
  cursor: not-allowed;
  opacity: 0.4;
}

.input-hint {
  text-align: center;
  font-size: 11px;
  color: var(--ink-faint);
  margin-top: 8px;
}

@media (max-width: 640px) {
  .example-cards {
    grid-template-columns: 1fr;
  }
  .chat-header, .messages-wrap, .input-area {
    padding-left: 16px;
    padding-right: 16px;
  }
}
</style>
