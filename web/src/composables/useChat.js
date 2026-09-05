import { computed, reactive, ref } from 'vue'
import { useSettings } from './useSettings'

// ============================================================
// useChat — 会话状态 + WebSocket 事件流
//
// 新增：
//   - draft 草稿自动保存到 localStorage（刷新不丢输入）
//   - 按会话独立记忆草稿（切换会话恢复各自的草稿）
//   - sessionPreviewMap：会话 ID → 标题预览（给 Sidebar/Header 显示标题用）
// ============================================================

const DRAFT_KEY = 'crawagent-draft'  // localStorage key：草稿根

const newId = () =>
  Array.from(crypto.getRandomValues(new Uint8Array(6)), b => b.toString(16).padStart(2, '0')).join('')

// 单例：跨组件共享状态
let _instance = null
export function useChat() {
  if (_instance) return _instance
  _instance = createChat()
  return _instance
}

function createChat() {
  const settings = useSettings() // 发送消息时携带当前选中的模型
  const items = reactive([])
  const busy = ref(false)
  const connected = ref(false)
  const typing = ref(false)
  // 当前子 Agent（video_site_expert 等）的最新进度里程碑，用于 typing 指示器实时显示
  const currentProgress = ref('')
  // 按会话独立记忆草稿：切换会话不丢输入
  const draftBySession = reactive({}) // session_id → string
  const lastDraft = ref('')
  // 每个会话独立记忆自己的状态栏：切换会话不丢失
  const statusBySession = reactive({})
  const lastStatus = computed(() => statusBySession[session.value] || null)
  const sessions = ref([]) // 侧栏会话列表 [{id, preview}]

  let sessionId = null
  try { sessionId = localStorage.getItem('crawagent-session') } catch (e) { /* ignore */ }
  if (!sessionId) sessionId = newId()
  const session = ref(sessionId)

  // 初始加载草稿（当前会话）
  try {
    const all = JSON.parse(localStorage.getItem(DRAFT_KEY) || '{}')
    Object.assign(draftBySession, all)
  } catch (e) { /* ignore */ }
  // 当前会话草稿
  const draft = computed({
    get: () => draftBySession[session.value] || '',
    set: (v) => {
      draftBySession[session.value] = v || ''
      persistDraft()
    },
  })

  function persistDraft() {
    try { localStorage.setItem(DRAFT_KEY, JSON.stringify(draftBySession)) } catch (e) { /* ignore */ }
  }

  let ws = null
  let sessionPoll = null // 任务进行中刷新侧栏列表的定时器
  let currentTrace = null // 当前轮的轨迹对象（响应式，直接 push 进 items）
  let traceRoundId = 0 // 轨迹轮次计数器，用于区分不同对话轮次的轨迹
  const aiStreams = new Map() // msg.id → items 中正在流式增长的 ai 条目
  const _itemIdCounter = ref(0) // 全局唯一 item ID 计数器

  function nextItemId() {
    _itemIdCounter.value++
    return `item_${_itemIdCounter.value}`
  }

  function persistSession() {
    try { localStorage.setItem('crawagent-session', session.value) } catch (e) { /* ignore */ }
  }

  // ---------- 事件 → items ----------

  function pushUser(content) {
    currentTrace = null
    items.push({ kind: 'user', content, _id: nextItemId() })
    draftBySession[session.value] = ''
    persistDraft()
  }

  function pushAi(content) {
    items.push({ kind: 'ai', content, _id: nextItemId() })
  }

  function ensureTrace() {
    if (!currentTrace) {
      traceRoundId++
      currentTrace = reactive({
        kind: 'trace',
        steps: [],
        _expanded: true,
        roundId: traceRoundId,
        _id: nextItemId(),
      })
      items.push(currentTrace)
      console.log('[trace] created trace#' + traceRoundId, 'id=' + currentTrace._id)
    }
    return currentTrace
  }

  function pushToolCall(name, args, toolCallId) {
    const step = { name, args, result: null, done: false }
    if (toolCallId) step.toolCallId = toolCallId
    ensureTrace().steps.push(step)
    console.log('[trace] pushToolCall', name, '→ total steps:', ensureTrace().steps.length)
  }

  function pushToolResult(content, toolCallId) {
    const trace = ensureTrace()
    let step = null
    if (toolCallId) {
      step = trace.steps.find(s => s.toolCallId === toolCallId && !s.done)
    }
    if (!step) {
      step = trace.steps[trace.steps.length - 1]
      if (!step || step.done) {
        step = { name: 'tool', args: '', result: null, done: false }
        trace.steps.push(step)
      }
    }
    step.result = content
    step.done = true
  }

  function pushStatus(line) {
    statusBySession[session.value] = line
  }

  function pushThinking(content) {
    // 思考轨迹不生成独立卡片，而是作为 trace 的 thinking step 嵌入时间线
    // （放在 tool_call 之前，形成"我的计划 → 调用工具 → 拿到结果"的顺序）
    const trace = ensureTrace()
    trace.steps.push({ kind: 'thinking', content, done: true })

    // 关键去重：流式过程中，AI 会用 ai_delta 先把"工具调用前的计划"流式显示成一个
    // AI 卡片。等 ai_thinking 事件到来时，确认该尾部 AI 卡片内容就是这段思考，
    // 就把它删掉，避免"同一句话在 AI 卡片 + Thinking step 里出现两次"。
    const trimmedCmp = (a, b) =>
      (a || '').replace(/\s+/g, ' ').trim() === (b || '').replace(/\s+/g, ' ').trim()
    while (items.length) {
      const tail = items[items.length - 1]
      // AI 卡片在最末尾，内容和 thinking 一致 → 就是 plan 流式留下的重复
      if (tail.kind === 'ai' && trimmedCmp(tail.content, content)) {
        items.pop()
        // 清理 aiStreams 里对应的残项
        for (const [k, v] of aiStreams.entries()) {
          if (v === tail) aiStreams.delete(k)
        }
        console.log('[trace] pushThinking: 移除尾部重复 AI 卡片')
        break
      }
      // 尾部是 trace / tool step 未闭合时 trace 还没切走，正常
      if (tail.kind === 'trace') {
        break
      }
      // 其他类型（thinking/tool 等）不应出现在独立 items 里，理论不会
      break
    }

    console.log('[trace] pushThinking → trace steps:', trace.steps.length)
  }

  function pushAiDelta(id, delta) {
    let item = aiStreams.get(id)
    if (!item) {
      item = { kind: 'ai', content: '', streaming: true, _id: nextItemId() }
      items.push(item)
      aiStreams.set(id, item)
    }
    item.content += delta
  }

  function finishAiStream(id) {
    const item = aiStreams.get(id)
    if (item) item.streaming = false
    aiStreams.delete(id)
  }

  function pushProgress(evt) {
    // progress 事件来自 server 心跳，对应子 Agent（video_site_expert）的实时里程碑。
    // 找到当前 trace 中最后一个 "未完成的 tool_call"（通常就是 video_site_expert()），
    // 把新行追加到它的 progress_lines 里，在 CrawlTrace 中作为内联日志展示。
    if (!evt || !Array.isArray(evt.lines) || !evt.lines.length) return
    const trace = currentTrace
    if (!trace || !trace.steps.length) {
      // 还没 trace 就先放全局缓冲（不太常见），避免丢失
      return
    }
    let step = null
    // 优先匹配：最后一个 done=false 的 tool step（即正在跑的那个）
    for (let i = trace.steps.length - 1; i >= 0; i--) {
      const s = trace.steps[i]
      if (s.kind !== 'thinking' && !s.done) { step = s; break }
    }
    if (!step) {
      // 没找到，就放最后一个 tool step
      for (let i = trace.steps.length - 1; i >= 0; i--) {
        if (trace.steps[i].kind !== 'thinking') { step = trace.steps[i]; break }
      }
    }
    if (!step) return
    if (!step.progress_lines) step.progress_lines = []
    for (const line of evt.lines) {
      if (!line) continue
      // 避免完全相同的重复
      if (step.progress_lines[step.progress_lines.length - 1] === line) continue
      step.progress_lines.push(line)
      // 同步更新全局最新进度，供 typing 指示器显示
      currentProgress.value = line
    }
    console.log('[progress] lines:', evt.lines.length, 'attached to tool step:', step.name || '(unknown)')
  }

  function pushError(message) {
    items.push({ kind: 'error', content: `⚠ ${message}（输入内容已保留，可直接重发）`, _id: nextItemId() })
  }

  function handleEvent(e) {
    switch (e.type) {
      case 'tool_call': pushToolCall(e.name, e.args, e.tool_call_id); break
      case 'tool_result': pushToolResult(e.content, e.tool_call_id); break
      case 'ai_thinking': pushThinking(e.content); break
      case 'ai_delta': pushAiDelta(e.id, e.delta); break
      case 'progress': pushProgress(e); break
      case 'ai_done': finishAiStream(e.id); break
      case 'ai': pushAi(e.content); break
      case 'status':
        pushStatus(e.line)
        break
      case 'resumed':
        // 刷新页面后重连，后端告知该会话仍有任务在跑 → 恢复红色停止按钮
        busy.value = true
        typing.value = true
        startSessionPoll()
        break
      case 'done': endTurn(); fetchSessions();
        // 后台自动命名（首轮）需要 1-3s 跑完，延迟再拉一次拿标题
        setTimeout(fetchSessions, 4000);
        break
      case 'error':
        endTurn()
        pushError(e.message || '未知错误')
        break
    }
  }

  function endTurn() {
    busy.value = false
    typing.value = false
    currentProgress.value = ''
    currentTrace = null
    aiStreams.clear()
    if (sessionPoll) { clearInterval(sessionPoll); sessionPoll = null }
  }

  // 任务进行中轮询刷新侧栏会话列表：新会话发首条消息时 checkpoint 尚未
  // 落库，列表里看不到正在跑的会话；轮询直到 done（endTurn）自动停止
  function startSessionPoll() {
    if (sessionPoll) return
    sessionPoll = setInterval(fetchSessions, 3000)
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
      traceRoundId = 0
      for (const m of data.messages || []) {
        if (m.role === 'user') {
          if (currentTrace && currentTrace.steps.length > 0) {
            currentTrace = null
          }
          pushUser(m.content)
        }
        else if (m.role === 'ai') pushAi(m.content)
        else if (m.role === 'tool_call') pushToolCall(m.name, m.args, m.tool_call_id)
        else if (m.role === 'tool_result') pushToolResult(m.content, m.tool_call_id)
        else if (m.role === 'thinking') pushThinking(m.content)
      }
      currentTrace = null
      if (data.status) statusBySession[session.value] = data.status
      console.log('[loadHistory] loaded', data.messages?.length || 0, 'messages, items:', items.map(i => i.kind + (i.steps ? `(${i.steps.length}s)` : '')))
    } catch (e) {
      console.error('[loadHistory] failed:', e)
    }
  }

  // ---------- 发送与新会话 ----------

  function send(text) {
    const content = (text || '').trim()
    if (!content || busy.value) return false
    lastDraft.value = content // 失败时恢复输入用
    if (!ws || ws.readyState !== WebSocket.OPEN) {
      pushError('连接尚未就绪，请稍候或点击横幅重连')
      return false
    }
    pushUser(content)
    busy.value = true
    typing.value = true
    startSessionPoll()
    ws.send(JSON.stringify({ type: 'message', content, model: settings.state.model }))
    return true
  }

  // ---------- 会话列表与切换 ----------

  async function fetchSessions() {
    try {
      const r = await fetch('/api/sessions')
      if (!r.ok) return
      const data = await r.json()
      sessions.value = data.sessions || []
    } catch (e) { /* ignore */ }
  }

  function switchSession(id) {
    if (!id || id === session.value) return
    endTurn()
    session.value = id
    persistSession()
    items.splice(0, items.length)
    currentTrace = null
    traceRoundId = 0
    aiStreams.clear()
    connect()
    loadHistory()
  }

  async function _removeSession(id, { archive }) {
    // 通用：归档或彻底删除的共享逻辑
    const wasCurrent = id === session.value
    if (wasCurrent) {
      newSession(false)
    }
    try {
      const url = archive
        ? `/api/sessions/${encodeURIComponent(id)}/archive`
        : `/api/sessions/${encodeURIComponent(id)}`
      const method = archive ? 'POST' : 'DELETE'
      const r = await fetch(url, { method })
      if (!r.ok) {
        await fetchSessions()
        if (wasCurrent) {
          session.value = id
          connect()
          loadHistory()
        }
        return
      }
    } catch (e) { /* ignore */ }

    if (draftBySession[id]) {
      delete draftBySession[id]
      persistDraft()
    }
    await fetchSessions()
    if (wasCurrent) {
      connect()
    }
  }

  async function deleteSession(id) {
    // 彻底删除：不归档，不可恢复
    await _removeSession(id, { archive: false })
  }

  async function archiveSession(id) {
    // 归档：导出到 logs/ 目录后从列表移除
    await _removeSession(id, { archive: true })
  }

  function newSession(doConnect = true) {
    endTurn()
    session.value = newId()
    persistSession()
    items.splice(0, items.length)
    currentTrace = null
    traceRoundId = 0
    aiStreams.clear()
    if (doConnect) connect()
  }

  function stop() {
    if (!busy.value) return
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: 'stop' }))
    }
    endTurn()
  }

  async function batchDeleteSessions(ids) {
    if (!ids?.length) return { ok: false, deleted: 0 }
    try {
      const r = await fetch('/api/sessions/bulk-delete', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ids }),
      })
      if (!r.ok) return { ok: false, deleted: 0 }
      const data = await r.json()
      // 清理本地草稿
      for (const id of ids) {
        if (draftBySession[id]) { delete draftBySession[id] }
      }
      persistDraft()
      // 如果删除了当前会话，创建新会话
      if (ids.includes(session.value)) {
        newSession(false)
        connect()
      }
      await fetchSessions()
      return data
    } catch (e) {
      return { ok: false, deleted: 0 }
    }
  }

  function reconnect() {
    connect()
    loadHistory()
  }

  async function renameSession(id, title) {
    try {
      const r = await fetch(`/api/sessions/${encodeURIComponent(id)}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title }),
      })
      if (!r.ok) return
      // 乐观更新本地列表，免去重新拉取
      const s = sessions.value.find(x => x.id === id)
      if (s) s.title = title || undefined
    } catch (e) { /* ignore */ }
  }

  persistSession()

  return {
    // state
    items, busy, connected, typing, session, lastDraft, lastStatus, sessions, draft, currentProgress,
    // actions
    connect, loadHistory, send, stop, newSession, switchSession, fetchSessions, reconnect, deleteSession,
    archiveSession, batchDeleteSessions, renameSession,
  }
}
