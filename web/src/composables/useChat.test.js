// useChat 事件分发测试（handoff §1.5）— Mock WebSocket + fetch，全离线。
// useChat 是模块级单例：每个用例 vi.resetModules() 后动态 import 拿全新实例。
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

let MockWebSocket

beforeEach(() => {
  vi.resetModules()
  MockWebSocket = class {
    static OPEN = 1
    static instances = []
    constructor(url) {
      this.url = url
      this.readyState = 0
      MockWebSocket.instances.push(this)
    }
    send(payload) { this.__lastSent = payload }
    close() {}
  }
  vi.stubGlobal('WebSocket', MockWebSocket)
  vi.stubGlobal('fetch', vi.fn(async () => ({ ok: false, json: async () => ({}) })))
})

afterEach(() => {
  vi.unstubAllGlobals()
})

async function setup() {
  const mod = await import('./useChat')
  const chat = mod.useChat()
  chat.connect()
  const ws = MockWebSocket.instances.at(-1)
  ws.onopen?.()
  ws.readyState = 1
  return { chat, ws }
}

function emit(ws, event) {
  ws.onmessage?.({ data: JSON.stringify(event) })
}

describe('useChat 事件分发', () => {
  it('用例 1：tool_call → tool_result 生成一条完整工具气泡', async () => {
    const { chat, ws } = await setup()
    emit(ws, { type: 'tool_call', name: 'browse_and_crawl', args: '{"url":"https://x"}', tool_call_id: 't1' })
    emit(ws, { type: 'tool_result', content: 'page fetched', tool_call_id: 't1' })

    const trace = chat.items.find(i => i.kind === 'trace')
    expect(trace).toBeTruthy()
    expect(trace.steps.length).toBe(1)
    const step = trace.steps[0]
    expect(step.name).toBe('browse_and_crawl')
    expect(step.result).toBe('page fetched')
    expect(step.done).toBe(true)
  })

  it('用例 2：ai_delta 三段流式拼接为一条完整 AI 消息', async () => {
    const { chat, ws } = await setup()
    emit(ws, { type: 'ai_delta', id: 'm1', delta: '你' })
    emit(ws, { type: 'ai_delta', id: 'm1', delta: '好' })
    emit(ws, { type: 'ai_delta', id: 'm1', delta: '呀' })
    emit(ws, { type: 'ai_done', id: 'm1' })

    const aiItems = chat.items.filter(i => i.kind === 'ai')
    expect(aiItems.length).toBe(1)
    expect(aiItems[0].content).toBe('你好呀')
    expect(aiItems[0].streaming).toBe(false)
  })

  it('用例 3：error 事件追加错误气泡，后续输入可继续不卡', async () => {
    const { chat, ws } = await setup()
    chat.busy.value = true
    chat.typing.value = true
    emit(ws, { type: 'error', message: '工具爆炸了' })

    const err = chat.items.find(i => i.kind === 'error')
    expect(err).toBeTruthy()
    expect(err.content).toContain('工具爆炸了')
    expect(chat.busy.value).toBe(false)
    expect(chat.typing.value).toBe(false)

    // 错误后可继续发送（连接已就绪、busy 已复位）
    const sent = chat.send('再来一次')
    expect(sent).toBe(true)
    expect(chat.items.some(i => i.kind === 'user' && i.content === '再来一次')).toBe(true)
  })

  it('用例 4：重连游标 0 重放 10 条事件 → 消息数正确且无重复', async () => {
    const { chat, ws } = await setup()
    // 模拟后端重连时从 cursor 0 全量重放的一轮事件（共 10 条）
    const replay = [
      { type: 'tool_call', name: 'crawl_webpage', args: '{}', tool_call_id: 't1' },
      { type: 'tool_result', content: 'r1', tool_call_id: 't1' },
      { type: 'ai_thinking', content: '我的计划' },
      { type: 'ai_delta', id: 'm1', delta: '回' },
      { type: 'ai_delta', id: 'm1', delta: '答' },
      { type: 'ai_delta', id: 'm1', delta: '完毕' },
      { type: 'ai_done', id: 'm1' },
      { type: 'progress', lines: ['子代理: 开始搜索'] },
      { type: 'status', line: '1 轮 · 3 步' },
      { type: 'done' },
    ]
    for (const e of replay) emit(ws, e)

    // 1 条 trace（含 1 个工具 step + 1 个 thinking step，无重复 step）+ 1 条 AI 回复
    const traces = chat.items.filter(i => i.kind === 'trace')
    const aiItems = chat.items.filter(i => i.kind === 'ai')
    expect(traces.length).toBe(1)
    expect(traces[0].steps.length).toBe(2)
    expect(traces[0].steps.filter(s => s.name === 'crawl_webpage').length).toBe(1)
    expect(aiItems.length).toBe(1)
    expect(aiItems[0].content).toBe('回答完毕')
    expect(chat.items.length).toBe(2)
    expect(chat.busy.value).toBe(false)
  })

  it('用例 4b：ai_thinking_delta 流式累积成一条 thinking step，done 收尾，tool_call 强制收尾', async () => {
    const { chat, ws } = await setup()
    const events = [
      { type: 'ai_thinking_delta', id: 'r1', delta: '先想' },
      { type: 'ai_thinking_delta', id: 'r1', delta: '一步' },
      { type: 'ai_thinking_delta', id: 'r1', delta: '…' },
      { type: 'ai_thinking_done', id: 'r1' },
      { type: 'tool_call', name: 'crawl_webpage', args: '{}', tool_call_id: 't1' },
      { type: 'tool_result', content: 'r1', tool_call_id: 't1' },
      { type: 'done' },
    ]
    for (const e of events) emit(ws, e)

    const traces = chat.items.filter(i => i.kind === 'trace')
    expect(traces.length).toBe(1)
    const thinkSteps = traces[0].steps.filter(s => s.kind === 'thinking')
    // 多个 delta 只生成一条 step，内容累积
    expect(thinkSteps.length).toBe(1)
    expect(thinkSteps[0].content).toBe('先想一步…')
    expect(thinkSteps[0].streaming).toBe(false) // done 已收尾
    // tool_call 被收入同一条 trace
    expect(traces[0].steps.filter(s => s.name === 'crawl_webpage').length).toBe(1)
  })

  it('用例 4c：流式中途收到 tool_call（无 done）也安全收尾，不残留 streaming', async () => {
    const { chat, ws } = await setup()
    const events = [
      { type: 'ai_thinking_delta', id: 'r2', delta: '想' },
      { type: 'tool_call', name: 'crawl_webpage', args: '{}', tool_call_id: 't2' },
      { type: 'done' },
    ]
    for (const e of events) emit(ws, e)

    const trace = chat.items.find(i => i.kind === 'trace')
    const think = trace.steps.find(s => s.kind === 'thinking')
    expect(think).toBeTruthy()
    expect(think.content).toBe('想')
    expect(think.streaming).toBe(false) // pushToolCall 的安全网兜住了
  })

  it('用例 4d：reasoning 双写 content 时，ai_thinking_done 移除与思考重复的 AI 卡片', async () => {
    const { chat, ws } = await setup()
    // 模拟供应商把同一推理文本同时写进 reasoning_content 与 content：
    // ai_thinking_delta 建 thinking step，ai_delta 建了一张同文 AI 卡片
    const events = [
      { type: 'ai_thinking_delta', id: 'r3', delta: '我想想' },
      { type: 'ai_delta', id: 'r3', delta: '我想想' }, // 双写：content 也喂了同一文本
      { type: 'ai_thinking_done', id: 'r3' },
      { type: 'ai_delta', id: 'r3', delta: '正经回答' }, // 思考结束后的真实回答
      { type: 'ai_done', id: 'r3' },
      { type: 'done' },
    ]
    for (const e of events) emit(ws, e)

    const trace = chat.items.find(i => i.kind === 'trace')
    const think = trace.steps.find(s => s.kind === 'thinking')
    expect(think).toBeTruthy()
    expect(think.content).toBe('我想想')
    expect(think.streaming).toBe(false)
    // 双写期间建的同文 AI 卡片应被 finishThinkingStream 移除；
    // 真实回答卡片保留，且内容不含"我想想"
    const aiItems = chat.items.filter(i => i.kind === 'ai')
    expect(aiItems.length).toBe(1)
    expect(aiItems[0].content).toBe('正经回答')
  })

  it('用例 5：/new（newSession）→ sessionId 更换、messages 清空', async () => {
    const { chat, ws } = await setup()
    emit(ws, { type: 'ai', content: '旧消息' })
    expect(chat.items.length).toBe(1)

    const oldSession = chat.session.value
    chat.newSession()

    expect(chat.session.value).not.toBe(oldSession)
    expect(chat.items.length).toBe(0)
    // 新会话建了新连接
    expect(MockWebSocket.instances.at(-1)).not.toBe(ws)
  })

  it('用例 6：ask 选择题 → 卡片渲染，answerAsk 经 WS 回传，ask_answered 锁定所选项', async () => {
    const { chat, ws } = await setup()
    emit(ws, { type: 'ask', ask_id: 'ask_1', question: '要打开 anything-analyzer 吗？', options: ['打开', '暂不'] })

    const card = chat.items.find(i => i.kind === 'ask')
    expect(card).toBeTruthy()
    expect(card.question).toBe('要打开 anything-analyzer 吗？')
    expect(card.options).toEqual(['打开', '暂不'])
    expect(card.answered).toBeUndefined()

    expect(chat.answerAsk('ask_1', '打开')).toBe(true)
    const sent = MockWebSocket.instances.at(-1).__lastSent
    expect(JSON.parse(sent)).toEqual({ type: 'ask_answer', ask_id: 'ask_1', value: '打开' })

    emit(ws, { type: 'ask_answered', ask_id: 'ask_1', value: '打开' })
    expect(card.answered).toBe('打开')
  })

  it('用例 7：ask 超时（ask_answered value=null）→ 卡片标记为未答', async () => {
    const { chat, ws } = await setup()
    emit(ws, { type: 'ask', ask_id: 'ask_2', question: '授权批量下载？', options: ['继续', '取消'] })
    emit(ws, { type: 'ask_answered', ask_id: 'ask_2', value: null })

    const card = chat.items.find(i => i.kind === 'ask')
    expect(card.answered).toBeNull()
  })
})
