<template>
  <div class="chat-page">
    <!-- 顶部栏 -->
    <header class="chat-header">
      <div class="header-left">
        <h2 class="font-display">{{ currentSessionName }}</h2>
        <span class="session-id" v-if="sessionId">#{{ sessionId.slice(0, 8) }}</span>
      </div>
      <div class="header-right">
        <!-- Token 用量统计徽章（本会话累计，含缓存命中率与费用） -->
        <div class="usage-stats" v-if="hasUsage">
          <span class="usage-badge" title="输入 + 输出 token 总量">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <rect x="3" y="4" width="18" height="16" rx="2"/>
              <path d="M3 10h18M9 4v6"/>
            </svg>
            {{ usageTotal.total_tokens }} tokens
          </span>
          <span class="usage-badge" :class="{ 'rate-good': hitRate >= 0.9, 'rate-bad': hitRate > 0 && hitRate < 0.9 }" title="缓存命中率 = 命中 / (命中 + 未命中)">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <path d="M13 2L3 14h7l-1 8 10-12h-7l1-8z"/>
            </svg>
            缓存 {{ (hitRate * 100).toFixed(1) }}%
          </span>
          <span class="usage-badge" title="按 DeepSeek 官方价估算（命中 0.02/未命中 1/输出 2 元每百万）">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <circle cx="12" cy="12" r="10"/>
              <path d="M12 6v12M15 9.5c0-1.5-1.34-2.5-3-2.5s-3 1-3 2.5 1.5 2 3 2.5 3 1 3 2.5-1.34 2.5-3 2.5-3-1-3-2.5"/>
            </svg>
            ¥{{ usageTotal.cost_yuan }}
          </span>
        </div>
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
            <div class="msg-role">{{ msg.role === 'user' ? '你' : msg.role === 'tool' ? '工具' : '助手' }}</div>
            <div class="msg-text" v-if="msg.content && msg.content.trim()" :class="{ 'msg-text-tool': msg.role === 'tool' }" v-html="renderContent(msg.content)"></div>
            <div class="msg-text msg-text-empty" v-else-if="msg.toolCalls && msg.toolCalls.length">（已调用工具，见下方工具标签）</div>
            <div class="msg-text msg-text-empty" v-else-if="msg.role === 'tool'">工具执行完成</div>
            <div class="msg-text msg-text-empty" v-else>（无内容）</div>
            <div class="msg-tools" v-if="msg.toolCalls && msg.toolCalls.length">
              <div class="tool-chip" v-for="(tc, j) in msg.toolCalls" :key="j">
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                  <path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/>
                </svg>
                {{ getToolName(tc) }}
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
import { ref, computed, nextTick, onMounted, watch } from 'vue'
import { useRoute } from 'vue-router'
import { harnessApi } from '../api'

const route = useRoute()
const LS_SESSION_KEY = 'crawagent:last_session_id'
const LS_SESSION_NAME_KEY = 'crawagent:last_session_name'

const messages = ref([])
const inputText = ref('')
const loading = ref(false)
const sessionId = ref('')
const currentSessionName = ref('新对话')
const messagesWrap = ref(null)
const inputRef = ref(null)

// 会话累计 token 用量（含缓存命中率与费用）
const usageTotal = ref(null)
const hitRate = ref(0)
const hasUsage = computed(() => !!usageTotal.value && (usageTotal.value.total_tokens > 0))

const examples = [
  { icon: '📰', text: '爬取 Hacker News 首页标题' },
  { icon: '🛒', text: '监控某个商品价格变化' },
  { icon: '📚', text: '抓取一个小说网站的内容' },
  { icon: '🔍', text: '检查我的网站有没有安全漏洞' },
]

const inputPlaceholder = '想爬取什么？用大白话说就行…'

onMounted(async () => {
  // 1. 优先从 URL query 取 session_id（Tasks 页跳转过来带的参数）
  const urlSessionId = route.query.session_id
  if (urlSessionId && typeof urlSessionId === 'string') {
    sessionId.value = urlSessionId
    try {
      const hres = await harnessApi.listSessions(200)
      const list = hres.sessions || []
      const found = list.find(s => (s.id || s.session_id) === urlSessionId)
      if (found) {
        currentSessionName.value = found.name || '已有对话'
        await loadHistory()
        saveSessionToLocal()
        return
      }
    } catch (e) {
      console.warn('根据 URL session_id 查找失败:', e)
    }
  }

  // 2. 其次从 localStorage 恢复上次的会话
  let restoredFromLocal = false
  try {
    const lastSid = localStorage.getItem(LS_SESSION_KEY)
    const lastSname = localStorage.getItem(LS_SESSION_NAME_KEY)
    if (lastSid) {
      sessionId.value = lastSid
      currentSessionName.value = lastSname || '已有对话'
      await loadHistory()
      restoredFromLocal = true
    }
  } catch (e) {
    console.warn('从 localStorage 恢复会话失败:', e)
  }
  if (restoredFromLocal) return

  // 3. 最后尝试取最新的一个会话
  try {
    const res = await harnessApi.listSessions(1)
    const list = res.sessions || []
    if (list.length > 0) {
      sessionId.value = list[0].id || list[0].session_id
      currentSessionName.value = list[0].name || '已有对话'
      await loadHistory()
      saveSessionToLocal()
      return
    }
  } catch (e) {
    console.warn('获取会话列表失败:', e)
  }
  // 4. 完全没有会话则新建
  await newSession()
})

// 保存当前会话到 localStorage，刷新后可恢复
function saveSessionToLocal() {
  try {
    if (sessionId.value) localStorage.setItem(LS_SESSION_KEY, sessionId.value)
    if (currentSessionName.value) localStorage.setItem(LS_SESSION_NAME_KEY, currentSessionName.value)
  } catch { /* ignore */ }
}

// 加载会话累计 token 用量（刷新后恢复显示）
async function loadUsage() {
  if (!sessionId.value) return
  try {
    const res = await harnessApi.getUsage(sessionId.value)
    const u = res?.usage
    if (u) {
      usageTotal.value = u
      hitRate.value = u.hit_rate || 0
    }
  } catch (e) {
    console.warn('加载 token 用量失败:', e)
  }
}

// sessionId 变化时自动保存 + 加载累计用量
watch(sessionId, () => {
  saveSessionToLocal()
  loadUsage()
})
watch(currentSessionName, () => saveSessionToLocal())

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
    console.log('[Chat] loadHistory entries:', entries.length, 'sessionId:', sessionId.value)
    if (entries.length > 0) {
      const sorted = [...entries].sort((a, b) => (a.created_at || 0) - (b.created_at || 0))
      messages.value = sorted.map(e => ({
        role: e.role || 'assistant',
        content: (e.content || '').trim(),
        toolCalls: normalizeToolCalls(e.tool_calls || []),
        toolCallId: e.tool_call_id || '',
      }))
      console.log('[Chat] messages loaded:', messages.value.length)
    } else {
      console.log('[Chat] no entries found for session')
    }
    await scrollToBottom()
  } catch (e) {
    console.warn('加载历史失败:', e)
  }
}

// 兼容不同版本的 tool_calls 格式
function normalizeToolCalls(toolCalls) {
  if (!Array.isArray(toolCalls)) return []
  return toolCalls.map(tc => {
    // 格式 1: {function: {name: "xxx", arguments: {...}}, id: "xxx", type: "function"}
    if (tc.function && tc.function.name) {
      return {
        id: tc.id || '',
        name: tc.function.name,
        arguments: tc.function.arguments || {},
        type: tc.type || 'function',
      }
    }
    // 格式 2: {name: "xxx", args: {...}} (LangChain 格式)
    if (tc.name) {
      return {
        id: tc.id || '',
        name: tc.name,
        arguments: tc.args || tc.arguments || {},
        type: tc.type || 'function',
      }
    }
    // 格式 3: {tool_name: "xxx"} (旧格式)
    if (tc.tool_name) {
      return {
        id: tc.id || '',
        name: tc.tool_name,
        arguments: tc.arguments || {},
        type: tc.type || 'function',
      }
    }
    // 未知格式，返回原始数据
    return tc
  })
}

function getToolName(tc) {
  if (!tc) return '工具'
  // 兼容多种格式
  return tc.name || tc.function?.name || tc.tool_name || '未知工具'
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
      // 更新 token 用量徽章（累计值，兼容旧后端无 usage 字段）
      if (res.usage_total) {
        usageTotal.value = res.usage_total
        hitRate.value = res.usage_total.hit_rate || 0
      } else {
        await loadUsage()
      }
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
  if (!content || !content.trim()) return ''
  // 简单转义 HTML
  let html = content
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
  // 代码块（先用占位符保护，避免后续 \n→<br> 替换污染 pre 内部导致双行距）
  const codeBlocks = []
  html = html.replace(/```(\w*)\n?([\s\S]*?)```/g, (m, lang, code) => {
    const key = `__CRAWAGENT_CODE_${codeBlocks.length}__`
    codeBlocks.push(code)
    return `<pre class="code-block"><code>${key}</code></pre>`
  })
  // 行内代码
  html = html.replace(/`([^`]+)`/g, '<code class="inline-code">$1</code>')
  // 换行（代码块内容已替换为占位符，不会受影响）
  html = html.replace(/\n/g, '<br>')
  // 恢复代码块内容（内容已在上方转义，直接插入是安全的）
  html = html.replace(/__CRAWAGENT_CODE_(\d+)__/g, (m, i) => codeBlocks[+i])
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

/* Token 用量统计徽章 */
.usage-stats {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-right: 8px;
}

.usage-badge {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 4px 10px;
  background: var(--paper-warm);
  border: 1px solid var(--line);
  border-radius: 999px;
  font-size: 12px;
  color: var(--ink-soft);
  white-space: nowrap;
}

.usage-badge svg {
  flex-shrink: 0;
}

.usage-badge.rate-good {
  border-color: #34d399;
  color: #059669;
  background: #ecfdf5;
}

.usage-badge.rate-bad {
  border-color: #fbbf24;
  color: #b45309;
  background: #fffbeb;
}

@media (max-width: 720px) {
  .usage-stats {
    display: none; /* 窄屏隐藏徽章，避免挤压标题 */
  }
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

.msg-text-empty {
  font-size: 13px;
  color: var(--ink-faint);
  font-style: italic;
}

.msg-text-tool {
  background: var(--paper-warm);
  border: 1px dashed var(--line);
  border-radius: var(--radius-md);
  padding: 10px 12px;
  font-size: 12px;
  max-height: 300px;
  overflow-y: auto;
  word-break: break-all;
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
