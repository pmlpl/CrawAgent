// ChatPage 测试 — 聊天页基础渲染 + 工作文件夹提示条切换菜单（037）
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'

// Mock vue-router
vi.mock('vue-router', () => ({
  useRouter: () => ({ push: vi.fn() }),
  useRoute: () => ({ name: 'chat' }),
}))

// Mock useChat（可变 stub：测试直接摆状态后 mount）
vi.mock('../composables/useChat', () => {
  const state = {
    items: [],
    session: 'test-session',
    busy: false,
    typing: false,
    connected: false,
    connect: vi.fn(),
    reconnect: vi.fn(),
    loadHistory: vi.fn(),
    send: vi.fn(() => true),
    newSession: vi.fn(),
    switchSession: vi.fn(),
    deleteSession: vi.fn(),
    archiveSession: vi.fn(),
    batchDeleteSessions: vi.fn(),
    renameSession: vi.fn(),
    sessions: [],
    answerAsk: vi.fn(() => true),
    lastDraft: '',
    // 035/036：输入区「选择项目」与附件状态
    workDir: '',
    attachments: [],
    chooseProject: vi.fn(),
    notifyError: vi.fn(),
    addAttachment: vi.fn(),
    removeAttachment: vi.fn(),
    clearAttachments: vi.fn(),
    // 037：工作文件夹历史 + 菜单动作
    workDirHistory: [],
    fetchWorkDirHistory: vi.fn(),
    bindWorkDir: vi.fn(),
    clearWorkDir: vi.fn(),
  }
  globalThis.__chatPageChat = state
  return { useChat: () => state }
})

// Mock useSettings
vi.mock('../composables/useSettings', () => ({
  useSettings: () => ({
    state: { models: [], model: 'test-model', thinkingDepth: 'off' },
    config: { value: { defaultModel: 'test-model', thinkingDepth: 'off' } },
    saveThinking: vi.fn(),
    setSelectedModel: vi.fn(),
  }),
}))

import ChatPage from './ChatPage.vue'

function st() { return globalThis.__chatPageChat }

function freshState(overrides = {}) {
  const s = st()
  s.workDir = ''
  s.workDirHistory = []
  s.connected = false
  for (const k of ['fetchWorkDirHistory', 'bindWorkDir', 'clearWorkDir', 'chooseProject']) s[k].mockClear()
  Object.assign(s, overrides)
  return s
}

function mountPage(overrides = {}) {
  freshState(overrides)
  return mount(ChatPage, {
    global: {
      stubs: { CrawlTrace: true, AskCard: true, TransitionGroup: true },
    },
  })
}

describe('ChatPage', () => {
  it('渲染输入框', () => {
    const wrapper = mountPage()
    const hasInput = wrapper.find('textarea').exists() || wrapper.find('input[type="text"]').exists()
    expect(hasInput).toBe(true)
  })

  it('初始无消息时不崩溃', () => {
    const wrapper = mountPage()
    expect(wrapper.exists()).toBe(true)
  })

  // ---------- 工作文件夹提示条「切换项目」菜单（变更 037） ----------

  it('未选项目时不渲染提示条', () => {
    const wrapper = mountPage({ workDir: '' })
    expect(wrapper.find('.workdir-bar').exists()).toBe(false)
  })

  it('已选项目 → 提示条渲染 + 向上箭头按钮', () => {
    const wrapper = mountPage({ workDir: 'D:/projA' })
    const bar = wrapper.find('.workdir-bar')
    expect(bar.exists()).toBe(true)
    expect(bar.find('.wd-value').text()).toBe('D:/projA')
    expect(bar.find('.wd-switch').exists()).toBe(true)
  })

  it('点箭头 → 菜单弹出 + 拉历史', async () => {
    const wrapper = mountPage({ workDir: 'D:/projA' })
    await wrapper.find('.wd-switch').trigger('click')
    expect(st().fetchWorkDirHistory).toHaveBeenCalled()
    expect(wrapper.find('.wd-menu').exists()).toBe(true)
    // 空历史 → 显示「暂无历史项目」+ 两个动作项
    expect(wrapper.find('.wd-menu-empty').text()).toContain('暂无历史项目')
    const actions = wrapper.findAll('.wd-menu-action')
    expect(actions).toHaveLength(2)
    expect(actions[0].text()).toBe('打开新项目')
    expect(actions[1].text()).toBe('清除项目')
  })

  it('历史项点击 → bindWorkDir 绑定 + 菜单关闭', async () => {
    const wrapper = mountPage({ workDir: 'D:/projA', workDirHistory: ['D:/projB', 'D:/projC'] })
    await wrapper.find('.wd-switch').trigger('click')
    const items = wrapper.findAll('.wd-menu-item:not(.wd-menu-action)')
    expect(items).toHaveLength(2)
    expect(items[0].text()).toBe('D:/projB')
    await items[0].trigger('click')
    expect(st().bindWorkDir).toHaveBeenCalledWith('D:/projB')
    expect(wrapper.find('.wd-menu').exists()).toBe(false) // 关闭
  })

  it('打开新项目 → chooseProject + 菜单关闭', async () => {
    const wrapper = mountPage({ workDir: 'D:/projA' })
    await wrapper.find('.wd-switch').trigger('click')
    const actions = wrapper.findAll('.wd-menu-action')
    await actions[0].trigger('click') // 打开新项目
    expect(st().chooseProject).toHaveBeenCalled()
    expect(wrapper.find('.wd-menu').exists()).toBe(false)
  })

  it('清除项目 → clearWorkDir + 菜单关闭', async () => {
    const wrapper = mountPage({ workDir: 'D:/projA' })
    await wrapper.find('.wd-switch').trigger('click')
    const actions = wrapper.findAll('.wd-menu-action')
    await actions[1].trigger('click') // 清除项目
    expect(st().clearWorkDir).toHaveBeenCalled()
    expect(wrapper.find('.wd-menu').exists()).toBe(false)
  })

  it('点提示条外部 → 菜单关闭', async () => {
    const wrapper = mountPage({ workDir: 'D:/projA' })
    await wrapper.find('.wd-switch').trigger('click')
    expect(wrapper.find('.wd-menu').exists()).toBe(true)
    // 模拟点击提示条外部（document 级 click）
    document.body.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await wrapper.vm.$nextTick()
    expect(wrapper.find('.wd-menu').exists()).toBe(false)
  })

  it('再点箭头 → 收起菜单', async () => {
    const wrapper = mountPage({ workDir: 'D:/projA' })
    await wrapper.find('.wd-switch').trigger('click')
    expect(wrapper.find('.wd-menu').exists()).toBe(true)
    await wrapper.find('.wd-switch').trigger('click')
    expect(wrapper.find('.wd-menu').exists()).toBe(false)
  })
})
