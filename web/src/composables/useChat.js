import { computed, reactive, ref } from 'vue'
import { defineStore } from 'pinia'
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

// Pinia setup store：跨组件共享状态（原闭包工厂 createChat 包进 defineStore，
// 内部 617 行零改动——ws/定时器/aiStreams Map 等作为 setup 函数局部变量保非响应私有）
const useChatStore = defineStore('chat', () => {
  const settings = useSettings() // 发送消息时携带当前选中的模型
  const items = reactive([])
  const busy = ref(false)
  const connected = ref(false)
  const typing = ref(false)
  // 当前子 Agent（video_site_expert 等）的最新进度里程碑，用于 typing 指示器实时显示
  const currentProgress = ref('')
  // 工具运行实时计时：pushToolCall 记录 step.startedAt，nowTick 每秒跳动驱动 pill 实时显示。
  // 后端心跳里的"已耗时 Xs"是注入时刻的静态快照（工具结束就冻结），所以耗时由前端自己算。
  const nowTick = ref(0)
  let tickTimer = null
  function startTick() {
    if (tickTimer) return
    tickTimer = setInterval(() => { nowTick.value = Date.now() }, 1000)
  }
  function stopTick() {
    if (tickTimer) { clearInterval(tickTimer); tickTimer = null }
    nowTick.value = 0
  }
  // 按会话独立记忆草稿：切换会话不丢输入
  const draftBySession = reactive({}) // session_id → string
  const lastDraft = ref('')
  // 每个会话独立记忆自己的状态栏：切换会话不丢失
  const statusBySession = reactive({})
  const lastStatus = computed(() => statusBySession[session.value] || null)
  const sessions = ref([]) // 侧栏会话列表 [{id, preview}]

  // 正在执行的工具步：最后一个 trace 里最后一个未完成的 tool step
  // （LangGraph 顺序执行工具，同一时刻最多一个在跑）
  const runningTool = computed(() => {
    if (!busy.value) return null
    for (let i = items.length - 1; i >= 0; i--) {
      const it = items[i]
      if (it.kind !== 'trace') continue
      for (let j = it.steps.length - 1; j >= 0; j--) {
        const s = it.steps[j]
        if (s.kind !== 'thinking' && !s.done) return s
      }
      break
    }
    return null
  })
  // 实时已耗时秒数；无运行中工具 / 无起点 / 非 busy 时为 null
  const runningElapsed = computed(() => {
    const s = runningTool.value
    if (!s || !s.startedAt || !nowTick.value) return null
    return Math.max(0, Math.round((nowTick.value - s.startedAt) / 1000))
  })

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
        _expanded: false,
        roundId: traceRoundId,
        _id: nextItemId(),
      })
      items.push(currentTrace)
      console.log('[trace] created trace#' + traceRoundId, 'id=' + currentTrace._id)
    }
    return currentTrace
  }

  function pushToolCall(name, args, toolCallId) {
    _finalizeOpenThinking() // 工具调用开始 = 上一段思考（若有）已结束
    const step = { name, args, result: null, done: false, startedAt: Date.now() }
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
    // 工具结束，进度 pill 的里程碑随之失效 —— 否则 LLM 思考阶段会一直残留
    // 上一工具的旧文案（"运行中… 已耗时 5.6s" 冻结 bug 的另一半根因）
    currentProgress.value = ''
  }

  function pushStatus(line) {
    statusBySession[session.value] = line
  }

  function pushThinking(content, id) {
    // 思考轨迹不生成独立卡片，而是作为 trace 的 thinking step 嵌入时间线
    // （放在 tool_call 之前，形成"我的计划 → 调用工具 → 拿到结果"的顺序）
    const trace = ensureTrace()
    // 若已有同 id 的流式 thinking step（后端理论上发 done 而非整块），防御性收尾、不重复建
    if (id) {
      const existing = trace.steps.find(s => s.kind === 'thinking' && s._thinkId === id)
      if (existing) {
        existing.streaming = false
        return
      }
    }
    trace.steps.push({ kind: 'thinking', content, done: true, _thinkId: id, streaming: false, _opened: false })

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

  function pushThinkingDelta(id, delta) {
    // 推理模型思考 token 的流式增量：同 id 复用一条 thinking step，逐块追加。
    // 让用户在思考阶段就能看到推理过程实时滚动，而不是干等转圈。
    const trace = ensureTrace()
    let step = trace.steps.find(s => s.kind === 'thinking' && s._thinkId === id)
    if (!step) {
      step = { kind: 'thinking', content: delta, done: true, _thinkId: id, streaming: true, _opened: false }
      trace.steps.push(step)
      console.log('[trace] pushThinkingDelta start id=' + id)
    } else {
      step.content += delta
    }
  }

  function finishThinkingStream(id) {
    // 后端发 ai_thinking_done：该条思考流结束，标记收尾（折叠容器由 CrawlTrace 据 streaming 渲染）
    const trace = currentTrace
    if (!trace) return
    const step = trace.steps.find(s => s.kind === 'thinking' && s._thinkId === id)
    if (!step) return
    step.streaming = false
    // 该 id 的思考流结束；若正文里残留了同内容的 AI 卡片（部分供应商把推理同时
    // 写进 content 与 reasoning_content，导致 ai_delta 也建了一张同文卡片），移除。
    const cmp = (a, b) =>
      (a || '').replace(/\s+/g, ' ').trim() === (b || '').replace(/\s+/g, ' ').trim()
    while (items.length) {
      const tail = items[items.length - 1]
      if (tail.kind === 'ai' && cmp(tail.content, step.content)) {
        items.splice(items.length - 1, 1)
        // 注意 aiStreams 存的是原始对象，items 里是 reactive proxy，身份比较恒不等；
        // 直接按 id 删映射，否则后续同 id 的 ai_delta 会复用孤儿条目而不建新卡。
        aiStreams.delete(id)
        console.log('[trace] finishThinkingStream: 移除与思考重复的尾部 AI 卡片')
        break
      }
      break
    }
  }

  function _finalizeOpenThinking() {
    // 安全网：tool_call / ai_delta / 轮次结束时，强制收尾任何仍处于 streaming 的思考 step
    // （后端异常未发 done、或非推理路径误建时兜底，避免折叠容器卡在展开态）
    const trace = currentTrace
    if (!trace) return
    for (const s of trace.steps) {
      if (s.kind === 'thinking' && s.streaming) s.streaming = false
    }
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
      // 后端的"运行中… 已耗时 Xs"心跳是静态快照，不进 pill（仍留在轨迹日志里）；
      // pill 的耗时由前端 runningElapsed 实时计算
      if (!/^运行中…\s*已耗时\s*[\d.]+s$/.test(line)) currentProgress.value = line
    }
    console.log('[progress] lines:', evt.lines.length, 'attached to tool step:', step.name || '(unknown)')
  }

  function pushError(message, opts = {}) {
    const suffix = opts.restored
      ? `（上次失败于 ${opts.ts || '未知时间'}，下一轮成功后自动清除）`
      : '（输入内容已保留，可直接重发）'
    items.push({ kind: 'error', content: `⚠ ${message}${suffix}`, _id: nextItemId() })
  }

  // ---------- ask_user：AI 发起的选择题（human-in-the-loop） ----------

  function pushAsk(e) {
    currentTrace = null
    items.push({
      kind: 'ask',
      askId: e.ask_id,
      question: e.question,
      options: e.options || [],
      answered: undefined, // undefined=待答，字符串=用户已选，null=超时/未答
      _id: nextItemId(),
    })
  }

  function markAnswered(e) {
    for (let i = items.length - 1; i >= 0; i--) {
      const it = items[i]
      if (it.kind === 'ask' && it.askId === e.ask_id) {
        it.answered = e.value === undefined ? null : e.value
        break
      }
    }
  }

  function answerAsk(askId, value) {
    if (!ws || ws.readyState !== WebSocket.OPEN) return false
    ws.send(JSON.stringify({ type: 'ask_answer', ask_id: askId, value }))
    return true
  }

  function handleEvent(e) {
    switch (e.type) {
      case 'tool_call': pushToolCall(e.name, e.args, e.tool_call_id); break
      case 'tool_result': pushToolResult(e.content, e.tool_call_id); break
      case 'ai_thinking': pushThinking(e.content, e.id); break
      case 'ai_thinking_delta': pushThinkingDelta(e.id, e.delta); break
      case 'ai_thinking_done': finishThinkingStream(e.id); break
      case 'ai_delta': pushAiDelta(e.id, e.delta); break
      case 'progress': pushProgress(e); break
      case 'ask': pushAsk(e); break
      case 'ask_answered': markAnswered(e); break
      case 'ai_done': finishAiStream(e.id); break
      case 'ai': pushAi(e.content); break
      case 'status':
        pushStatus(e.line)
        break
      case 'resumed':
        // 刷新页面后重连，后端告知该会话仍有任务在跑 → 恢复红色停止按钮
        busy.value = true
        typing.value = true
        startTick()
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
    _finalizeOpenThinking() // 轮次结束兜底：收尾任何残留流式思考
    currentTrace = null
    aiStreams.clear()
    stopTick()
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
      // 恢复上次未解决的报错（后端持久化，下一轮成功才清除），刷新后红条不丢
      if (data.last_error && data.last_error.message) {
        pushError(data.last_error.message, { restored: true, ts: data.last_error.ts })
      }
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
    startTick()
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
    items, busy, connected, typing, session, lastDraft, lastStatus, sessions, draft, currentProgress, runningElapsed,
    // actions
    connect, loadHistory, send, stop, newSession, switchSession, fetchSessions, reconnect, deleteSession,
    archiveSession, batchDeleteSessions, renameSession, answerAsk,
  }
})

// 薄封装：消费方（App/ChatPage/ChatComposer/ContextRing/AskCard/Sites/SiteDetail/main）
// 全用 useChat()，setup store 返回的 ref/reactive/函数形态与原返回一致，零改动
export function useChat() {
  return useChatStore()
}
