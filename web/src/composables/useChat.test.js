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
    send() {}
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
})
