// ChatPage 测试 — 聊天页基础渲染：输入框 + 发送 + 消息列表
import { describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'

// Mock vue-router
vi.mock('vue-router', () => ({
  useRouter: () => ({ push: vi.fn() }),
  useRoute: () => ({ name: 'chat' }),
}))

// Mock useChat（聊天页核心状态）
const mockSend = vi.fn(() => true)
vi.mock('../composables/useChat', () => ({
  useChat: () => ({
    items: [],
    session: 'test-session',
    busy: false,
    typing: false,
    connected: false,
    connect: vi.fn(),
    reconnect: vi.fn(),
    loadHistory: vi.fn(),
    send: mockSend,
    newSession: vi.fn(),
    switchSession: vi.fn(),
    deleteSession: vi.fn(),
    archiveSession: vi.fn(),
    batchDeleteSessions: vi.fn(),
    renameSession: vi.fn(),
    sessions: [],
    answerAsk: vi.fn(() => true),
    lastDraft: '',
  }),
}))

// Mock useSettings
vi.mock('../composables/useSettings', () => ({
  useSettings: () => ({
    state: { model: 'test-model', thinkingDepth: 'off' },
    config: { value: { defaultModel: 'test-model', thinkingDepth: 'off' } },
  }),
}))

import ChatPage from './ChatPage.vue'

describe('ChatPage', () => {
  it('渲染输入框', () => {
    const wrapper = mount(ChatPage, {
      global: {
        stubs: {
          // Stub any child components that need complex setup
          CrawlTrace: true,
          AskCard: true,
          TransitionGroup: true,
        },
      },
    })
    // 聊天页应有输入区域
    const hasInput = wrapper.find('textarea').exists() || wrapper.find('input[type="text"]').exists()
    expect(hasInput).toBe(true)
  })

  it('初始无消息时不崩溃', () => {
    const wrapper = mount(ChatPage, {
      global: {
        stubs: { CrawlTrace: true, AskCard: true, TransitionGroup: true },
      },
    })
    // 不抛异常即通过
    expect(wrapper.exists()).toBe(true)
  })
})
