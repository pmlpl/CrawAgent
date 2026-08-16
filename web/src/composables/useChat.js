import { computed, reactive, ref } from 'vue'

// ============================================================
// useChat — 会话状态 + WebSocket 事件流
//
// items 是渲染用的消息流，元素类型：
//   { kind: 'user' | 'ai' | 'error', content }
//   { kind: 'trace', steps: [{ name, args, result, done }] }  // 爬行轨迹
//   { kind: 'thinking', content }                              // AI 推理/思考
// 独立状态：
//   lastStatus — 最近一次 turn 的状态栏（持久显示在输入框下方）
// 事件协议与后端 crawagent/web/server.py 一一对应。
// ============================================================

const newId = () =>
  Array.from(crypto.getRandomValues(new Uint8Array(6)), b => b.toString(16).padStart(2, '0')).join('')

export function useChat() {
  const items = reactive([])
  const busy = ref(false)
  const connected = ref(false)
  const typing = ref(false)
  const lastDraft = ref('')
  // 每个会话独立记忆自己的状态栏：切换会话不丢失，切回来立即恢复
  const statusBySession = reactive({}) // session_id → 该会话最近一次状态栏文案
  const lastStatus = computed(() => statusBySession[session.value] || null)
  const sessions = ref([]) // 侧栏会话列表 [{id, preview}]

  let sessionId = null
  try { sessionId = localStorage.getItem('crawagent-session') } catch (e) { /* ignore */ }
  if (!sessionId) sessionId = newId()
  const session = ref(sessionId)

  let ws = null
  let currentTrace = null // 当前轮的轨迹对象（响应式，直接 push 进 items）
  const aiStreams = new Map() // msg.id → items 中正在流式增长的 ai 条目（响应式代理）

  function persistSession() {
    try { localStorage.setItem('crawagent-session', session.value) } catch (e) { /* ignore */ }
  }

  // ---------- 事件 → items ----------

  function pushUser(content) {
    currentTrace = null
    items.push({ kind: 'user', content })
  }

  function pushAi(content) {
    items.push({ kind: 'ai', content })
  }

  function ensureTrace() {
    if (!currentTrace) {
      currentTrace = reactive({ kind: 'trace', steps: [] })
      items.push(currentTrace)
    }
    return currentTrace
  }

  function pushToolCall(name, args) {
    ensureTrace().steps.push({ name, args, result: null, done: false })
  }

  function pushToolResult(content) {
    const trace = ensureTrace()
    let step = trace.steps[trace.steps.length - 1]
    if (!step || step.done) {
      step = { name: 'tool', args: '', result: null, done: false }
      trace.steps.push(step)
    }
    step.result = content
    step.done = true
  }

  function pushStatus(line) {
    // 状态栏不插入聊天流，按会话记入映射表，常驻显示在输入框下方
    statusBySession[session.value] = line
  }

  function pushThinking(content) {
    items.push({ kind: 'thinking', content })
  }

  // token 级流式：同一条 AI 回复的增量挂到同一个条目上
  function pushAiDelta(id, delta) {
    let item = aiStreams.get(id)
    if (!item) {
      items.push({ kind: 'ai', content: '', streaming: true })
      item = items[items.length - 1] // 必须取响应式代理，裸对象变更不触发渲染
      aiStreams.set(id, item)
    }
    item.content += delta
  }

  function finishAiStream(id) {
    const item = aiStreams.get(id)
    if (item) item.streaming = false
    aiStreams.delete(id)
  }

  function pushError(message) {
    items.push({ kind: 'error', content: `⚠ ${message}（输入内容已保留，可直接重发）` })
  }

  function handleEvent(e) {
    switch (e.type) {
      case 'tool_call': typing.value = false; pushToolCall(e.name, e.args); break
      case 'tool_result': typing.value = false; pushToolResult(e.content); break
      case 'ai_thinking': pushThinking(e.content); break
      case 'ai_delta': typing.value = false; pushAiDelta(e.id, e.delta); break
      case 'ai_done': finishAiStream(e.id); break
      case 'ai': typing.value = false; pushAi(e.content); break
      case 'status': pushStatus(e.line); break
      case 'done': endTurn(); fetchSessions(); break
      case 'error':
        endTurn()
        pushError(e.message || '未知错误')
        break
    }
  }

  function endTurn() {
    busy.value = false
    typing.value = false
    currentTrace = null
    aiStreams.clear()
  }

  // ---------- WebSocket ----------

  function connect() {
    if (ws) { ws.onclose = null; ws.close() }
    const proto = location.protocol === 'https:' ? 'wss' : 'ws'
    ws = new WebSocket(`${proto}://${location.host}/ws/${session.value}`)
    ws.onopen = () => { connected.value = true }
    ws.onclose = () => { connected.value = false; endTurn() }
    ws.onerror = () => {}
    ws.onmessage = (ev) => {
      let e
      try { e = JSON.parse(ev.data) } catch (err) { return }
      handleEvent(e)
    }
  }

  // ---------- 历史恢复 ----------

  async function loadHistory() {
    try {
      const r = await fetch(`/api/history/${session.value}`)
      if (!r.ok) return
      const data = await r.json()
      currentTrace = null
      for (const m of data.messages || []) {
        if (m.role === 'user') pushUser(m.content)
        else if (m.role === 'ai') pushAi(m.content)
        else if (m.role === 'tool_call') pushToolCall(m.name, m.args)
        else if (m.role === 'tool_result') pushToolResult(m.content)
        else if (m.role === 'thinking') pushThinking(m.content)
      }
      currentTrace = null
      // 恢复该会话的状态栏（后端：内存指标或从检查点重建）
      if (data.status) statusBySession[session.value] = data.status
    } catch (e) { /* 历史恢复失败不阻塞使用 */ }
  }

  // ---------- 发送与新会话 ----------

  function send(text) {
    const content = (text || '').trim()
    if (!content || busy.value) return false
    lastDraft.value = content // 提前存草稿：任何失败路径都能恢复输入
    if (!ws || ws.readyState !== WebSocket.OPEN) {
      pushError('连接尚未就绪，请稍候或点击横幅重连')
      return false
    }
    pushUser(content)
    busy.value = true
    typing.value = true
    ws.send(JSON.stringify({ type: 'message', content }))
    return true
  }

  // ---------- 会话列表与切换 ----------

  async function fetchSessions() {
    try {
      const r = await fetch('/api/sessions')
      if (!r.ok) return
      const data = await r.json()
      sessions.value = data.sessions || []
    } catch (e) { /* 列表失败不阻塞使用 */ }
  }

  function switchSession(id) {
    if (busy.value || !id || id === session.value) return
    session.value = id
    persistSession()
    items.splice(0, items.length)
    currentTrace = null
    aiStreams.clear()
    connect()
    loadHistory()
  }

  function newSession() {
    if (busy.value) return
    session.value = newId()
    persistSession()
    items.splice(0, items.length)
    currentTrace = null
    aiStreams.clear()
    connect()
  }

  function reconnect() {
    connect()
    loadHistory()
  }

  persistSession()

  return {
    items, busy, connected, typing, session, lastDraft, lastStatus, sessions,
    connect, loadHistory, send, newSession, switchSession, fetchSessions, reconnect,
  }
}
