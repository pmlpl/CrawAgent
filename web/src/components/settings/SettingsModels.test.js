// SettingsModels 测试 — 对话模型页签：模型列表 + 浏览器子 Agent 卡片（032）
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'

// Mock useSettings（模块级单例，stub 最小可渲染状态）
const saveSettingsFields = vi.fn(async () => ({ ok: true, error: '' }))
const loadMock = vi.fn(async () => {})

vi.mock('../../composables/useSettings', () => ({
  useSettings: () => ({
    state: {
      loading: false,
      models: [{ name: 'glm-5.2', provider: '中转站' }],
      providers: [{ name: '中转站', base_url: 'http://x/v1', key_set: true }],
      testResult: null,
      saveTip: '',
      browserAgent: { model: 'glm-4v-flash', baseUrl: 'http://x/v1', apiKey: '****abcd' },
    },
    addModel: vi.fn(async () => true),
    updateModel: vi.fn(async () => true),
    deleteModel: vi.fn(async () => true),
    test: vi.fn(async () => {}),
    saveSettingsFields,
    load: loadMock,
  }),
}))

import SettingsModels from './SettingsModels.vue'

beforeEach(() => {
  saveSettingsFields.mockClear()
  loadMock.mockClear()
})

describe('SettingsModels', () => {
  it('渲染模型列表与浏览器子 Agent 卡片', () => {
    const wrapper = mount(SettingsModels)
    expect(wrapper.text()).toContain('模型')
    expect(wrapper.text()).toContain('浏览器子 Agent')
    expect(wrapper.text()).toContain('glm-5.2')
  })

  it('浏览器子 Agent 卡片回显三字段（key 脱敏）', () => {
    const wrapper = mount(SettingsModels)
    const inputs = wrapper.findAll('input')
    const values = inputs.map(i => i.element.value)
    expect(values).toContain('glm-4v-flash')
    expect(values).toContain('****abcd') // 脱敏掩码回显
  })

  it('保存浏览器子 Agent：提交三键并回读快照', async () => {
    const wrapper = mount(SettingsModels)
    const saveBtn = wrapper.findAll('button').find(b => b.text().includes('保存'))
    expect(saveBtn).toBeTruthy()
    await saveBtn.trigger('click')
    expect(saveSettingsFields).toHaveBeenCalledWith({
      browser_use_llm_model: 'glm-4v-flash',
      browser_use_llm_base_url: 'http://x/v1',
      browser_use_llm_api_key: '****abcd',
    })
    await wrapper.vm.$nextTick()
    expect(loadMock).toHaveBeenCalled() // 成功后回读快照
    expect(wrapper.text()).toContain('已保存并生效')
  })

  it('保存失败时展示错误提示', async () => {
    saveSettingsFields.mockResolvedValueOnce({ ok: false, error: 'Base URL 格式错误' })
    const wrapper = mount(SettingsModels)
    const saveBtn = wrapper.findAll('button').find(b => b.text().includes('保存'))
    await saveBtn.trigger('click')
    await wrapper.vm.$nextTick()
    expect(wrapper.text()).toContain('保存失败')
    expect(wrapper.text()).toContain('Base URL 格式错误')
    expect(loadMock).not.toHaveBeenCalled()
  })
})
