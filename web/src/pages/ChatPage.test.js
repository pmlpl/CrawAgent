// ChatPage 测试 — 聊天页基础渲染 + 工作文件夹提示条（037 菜单 → 038 1:1 锁定改版）
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
    // 037/038：工作文件夹历史（[{path, exists}] 新结构）+ 菜单动作
    workDirExists: true,
    workDirHistory: [],
    fetchWorkDirHistory: vi.fn(),
    refreshWorkDirExists: vi.fn(),
    bindWorkDir: vi.fn(),
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
  s.workDirExists = true
  s.workDirHistory = []
  s.connected = false
  for (const k of ['fetchWorkDirHistory', 'bindWorkDir', 'refreshWorkDirExists', 'chooseProject']) s[k].mockClear()
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

  // ---------- 工作文件夹提示条（038：未绑定弱化提示 / 已绑定路径 + 失效标注） ----------

  it('未选项目时提示条恒渲染（弱化态 + 引导文案）', () => {
    const wrapper = mountPage({ workDir: '' })
    const bar = wrapper.find('.workdir-bar')
    expect(bar.exists()).toBe(true) // 038：不再 v-if 隐藏，补上历史菜单入口
    expect(bar.classes()).toContain('wd-unbound')
    expect(wrapper.find('.wd-value-empty').text()).toContain('未选择项目')
    expect(wrapper.find('.wd-switch').exists()).toBe(true)
  })

  it('已选项目 → 提示条渲染路径 + 箭头按钮', () => {
    const wrapper = mountPage({ workDir: 'D:/projA' })
    const bar = wrapper.find('.workdir-bar')
    expect(bar.exists()).toBe(true)
    expect(bar.classes()).not.toContain('wd-unbound')
    expect(wrapper.find('.wd-value').text()).toBe('D:/projA')
    expect(wrapper.find('.wd-switch').exists()).toBe(true)
  })

  it('路径失效 → danger 标注「（文件夹已不存在）」', () => {
    const wrapper = mountPage({ workDir: 'D:/projA', workDirExists: false })
    expect(wrapper.find('.workdir-bar').classes()).toContain('wd-missing')
    expect(wrapper.find('.wd-value').text()).toContain('（文件夹已不存在）')
  })

  // ---------- 未绑定：历史项目菜单（038 用途反转 = 新会话快速初始化） ----------

  it('未绑定点箭头 → 历史菜单弹出 + 拉历史（含打开新项目，无清除）', async () => {
    const wrapper = mountPage({ workDir: '' })
    await wrapper.find('.wd-switch').trigger('click')
    expect(st().fetchWorkDirHistory).toHaveBeenCalled()
    expect(wrapper.find('.wd-menu').exists()).toBe(true)
    // 空历史 → 显示「暂无历史项目」+ 仅一个动作项（清除项目已随锁定移除）
    expect(wrapper.find('.wd-menu-empty').text()).toContain('暂无历史项目')
    const actions = wrapper.findAll('.wd-menu-action')
    expect(actions).toHaveLength(1)
    expect(actions[0].text()).toBe('打开新项目')
  })

  it('历史项点击 → bindWorkDir(path) + 菜单关闭', async () => {
    const wrapper = mountPage({
      workDir: '',
      workDirHistory: [{ path: 'D:/projB', exists: true }, { path: 'D:/projC', exists: true }],
    })
    await wrapper.find('.wd-switch').trigger('click')
    const items = wrapper.findAll('.wd-menu-item:not(.wd-menu-action)')
    expect(items).toHaveLength(2)
    expect(items[0].text()).toContain('D:/projB')
    await items[0].trigger('click')
    expect(st().bindWorkDir).toHaveBeenCalledWith('D:/projB')
    expect(wrapper.find('.wd-menu').exists()).toBe(false) // 关闭
  })

  it('失效历史项标灰 +「（已不存在）」标注，点击仍可绑定（主动重建）', async () => {
    const wrapper = mountPage({
      workDir: '',
      workDirHistory: [{ path: 'D:/alive', exists: true }, { path: 'D:/gone', exists: false }],
    })
    await wrapper.find('.wd-switch').trigger('click')
    const items = wrapper.findAll('.wd-menu-item:not(.wd-menu-action)')
    expect(items[0].classes()).not.toContain('wd-menu-stale')
    expect(items[1].classes()).toContain('wd-menu-stale')
    expect(items[1].text()).toContain('（已不存在）')
    await items[1].trigger('click')
    expect(st().bindWorkDir).toHaveBeenCalledWith('D:/gone')
  })

  it('未绑定打开新项目 → chooseProject + 菜单关闭', async () => {
    const wrapper = mountPage({ workDir: '' })
    await wrapper.find('.wd-switch').trigger('click')
    const actions = wrapper.findAll('.wd-menu-action')
    await actions[0].trigger('click') // 打开新项目
    expect(st().chooseProject).toHaveBeenCalled()
    expect(wrapper.find('.wd-menu').exists()).toBe(false)
  })

  // ---------- 已绑定：只读卡片（锁定，无换绑/清除入口） ----------

  it('已绑定点箭头 → 只读卡片（路径 + 存在状态，无操作项）', async () => {
    const wrapper = mountPage({ workDir: 'D:/projA' })
    await wrapper.find('.wd-switch').trigger('click')
    expect(wrapper.find('.wd-menu').exists()).toBe(true)
    expect(wrapper.find('.wd-menu-readonly').exists()).toBe(true)
    expect(wrapper.find('.wd-menu-readonly .wd-menu-path').text()).toBe('D:/projA')
    expect(wrapper.find('.wd-menu-status').text()).toContain('文件夹存在')
    // 锁定后无任何操作按钮
    expect(wrapper.findAll('.wd-menu-action')).toHaveLength(0)
    expect(wrapper.findAll('.wd-menu-item')).toHaveLength(0)
  })

  it('已绑定失效态 → 只读卡片警示文案', async () => {
    const wrapper = mountPage({ workDir: 'D:/projA', workDirExists: false })
    await wrapper.find('.wd-switch').trigger('click')
    const status = wrapper.find('.wd-menu-status')
    expect(status.classes()).toContain('stale')
    expect(status.text()).toContain('文件夹已不存在')
  })

  it('菜单无「清除项目」入口（038 锁定：防清除再绑别的绕过）', async () => {
    const wrapper = mountPage({ workDir: '' })
    await wrapper.find('.wd-switch').trigger('click')
    const texts = wrapper.findAll('.wd-menu-item').map(b => b.text())
    expect(texts.every(t => !t.includes('清除'))).toBe(true)
  })

  // ---------- 菜单开合 ----------

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
