// SettingsModels 测试 — 对话模型页签：模型列表 + 能力徽章/探测（033） + 浏览器子 Agent 卡片（032）
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'

const saveSettingsFields = vi.fn(async () => ({ ok: true, error: '' }))
const discoverModels = vi.fn(async () => ({ ok: true, models: [{ id: 'glm-4v-flash', owned_by: 'z' }, { id: 'glm-5.2', owned_by: 'z' }] }))
const probeModel = vi.fn(async () => ({ ok: true, model: 'glm-5.2', vision: false, vision_reply: '黑色', structured: true, structured_reply: '{"answer":"ok"}' }))
const saveManualVision = vi.fn(async () => true)
const loadMock = vi.fn(async () => {})

const MODELS = [
  { name: 'glm-5.2', provider: '中转站', caps: { vision: false, vision_source: 'probe', vision_manual: false, structured: true, vision_reply: '黑色', structured_reply: '{"answer":"ok"}' } },
  { name: 'glm-4v-flash', provider: '中转站', caps: { vision: true, vision_source: 'pattern', vision_manual: false, structured: null, vision_reply: '', structured_reply: '' } },
  { name: 'deepseek-chat', provider: 'DeepSeek', caps: null },
]

vi.mock('../../composables/useSettings', () => ({
  useSettings: () => ({
    state: {
      loading: false,
      models: MODELS,
      providers: [{ name: '中转站', base_url: 'http://x/v1', key_set: true }],
      testResult: null,
      saveTip: '',
      discovering: false,
      probing: '',
      browserAgent: { model: 'glm-4v-flash', baseUrl: 'http://x/v1', apiKey: '****abcd' },
    },
    addModel: vi.fn(async () => true),
    updateModel: vi.fn(async () => true),
    deleteModel: vi.fn(async () => true),
    test: vi.fn(async () => {}),
    saveSettingsFields,
    load: loadMock,
    discoverModels,
    probeModel,
    saveManualVision,
  }),
}))

import SettingsModels from './SettingsModels.vue'

const findBtn = (wrapper, text) => wrapper.findAll('button').find(b => b.text().includes(text))

beforeEach(() => {
  saveSettingsFields.mockClear()
  discoverModels.mockClear()
  probeModel.mockClear()
  saveManualVision.mockClear()
  loadMock.mockClear()
})

describe('SettingsModels：模型列表与能力徽章', () => {
  it('渲染模型表的能力列：探测过的显示徽章，未验证不显示', () => {
    const wrapper = mount(SettingsModels)
    expect(wrapper.text()).toContain('能力')
    expect(wrapper.text()).toContain('视觉 ✗')      // glm-5.2 探测过 = 明确不过
    expect(wrapper.text()).toContain('JSON ✓')
    expect(wrapper.text()).toContain('视觉 ✓推测')  // glm-4v-flash pattern 命中
    expect(wrapper.text()).not.toContain('?')       // 未验证不显示徽章（问号视觉噪音已去掉）
  })

  it('每行有测试按钮，点击调 probeModel 并展示模型原话', async () => {
    const wrapper = mount(SettingsModels)
    const btn = findBtn(wrapper, '测试')
    await btn.trigger('click')
    expect(probeModel).toHaveBeenCalledWith(MODELS[0])
    await wrapper.vm.$nextTick()
    await wrapper.vm.$nextTick()
    const text = wrapper.text()
    expect(text).toContain('探测结果')
    expect(text).toContain('黑色')           // 模型原话
    expect(text).toContain('{"answer":"ok"}')
  })

  it('手动勾选开关切换调 saveManualVision', async () => {
    const wrapper = mount(SettingsModels)
    const box = wrapper.find('input[type="checkbox"]')
    await box.setValue(true)
    expect(saveManualVision).toHaveBeenCalledWith(MODELS[0], true)
  })
})

describe('SettingsModels：获取模型列表', () => {
  it('Base URL 合法 + 有 Key 时显示获取按钮，点击调 discoverModels 并给出提示', async () => {
    const wrapper = mount(SettingsModels)
    await findBtn(wrapper, '添加模型').trigger('click')
    // 默认表单 DeepSeek 预设 baseUrl、Key 空 → 不可获取
    expect(findBtn(wrapper, '获取模型列表')).toBeUndefined()
    // 填入 Key → 可获取
    await wrapper.find('input[type="password"]').setValue('sk-typed')
    const btn = findBtn(wrapper, '获取模型列表')
    expect(btn).toBeTruthy()
    await btn.trigger('click')
    expect(discoverModels).toHaveBeenCalledWith({ provider: '', apiKey: 'sk-typed', baseUrl: 'https://api.deepseek.com/v1' })
    await wrapper.vm.$nextTick()
    expect(wrapper.text()).toContain('获取到 2 个模型')
  })

  it('discover 失败展示人话错误', async () => {
    discoverModels.mockResolvedValueOnce({ ok: false, error: '连不上 http://x/v1（检查地址是否正确、服务是否在跑）' })
    const wrapper = mount(SettingsModels)
    await findBtn(wrapper, '添加模型').trigger('click')
    await wrapper.find('input[type="password"]').setValue('sk-typed')
    await findBtn(wrapper, '获取模型列表').trigger('click')
    await wrapper.vm.$nextTick()
    expect(wrapper.text()).toContain('连不上')
  })
})

describe('SettingsModels：浏览器子 Agent', () => {
  it('渲染浏览器子 Agent 卡片与三键输入', () => {
    const wrapper = mount(SettingsModels)
    expect(wrapper.text()).toContain('浏览器子 Agent')
    const inputs = wrapper.findAll('input')
    const values = inputs.map(i => i.element.value)
    expect(values).toContain('****abcd') // 脱敏掩码回显
  })

  it('模型名下拉按能力排序：两维全过的排最前', () => {
    const wrapper = mount(SettingsModels)
    const selects = wrapper.findAll('select')
    const subSelect = selects.at(-1)
    const options = subSelect.findAll('option').map(o => o.element.value)
    // 第一个选项是跟随主模型，然后是池内模型（两维全过的 glm-4v-flash 在 deepseek-chat 前），最后自定义
    expect(options[0]).toBe('')
    const poolIdx = (name) => options.indexOf(name)
    expect(poolIdx('glm-4v-flash')).toBeLessThan(poolIdx('deepseek-chat'))
    expect(options.at(-1)).toBe('__custom__')
  })

  it('选择自定义出现手输框；选择池内模型回填 state', async () => {
    const wrapper = mount(SettingsModels)
    const selects = wrapper.findAll('select')
    const subSelect = selects.at(-1)
    await subSelect.setValue('__custom__')
    expect(wrapper.find('input[placeholder="输入模型 ID"]').exists()).toBe(true)
    await subSelect.setValue('glm-4v-flash')
    expect(wrapper.vm.state.browserAgent.model).toBe('glm-4v-flash')
    expect(wrapper.find('input[placeholder="输入模型 ID"]').exists()).toBe(false)
  })

  it('保存浏览器子 Agent：提交三键并回读快照', async () => {
    const wrapper = mount(SettingsModels)
    const saveBtn = findBtn(wrapper, '保存')
    await saveBtn.trigger('click')
    expect(saveSettingsFields).toHaveBeenCalledWith({
      browser_use_llm_model: 'glm-4v-flash',
      browser_use_llm_base_url: 'http://x/v1',
      browser_use_llm_api_key: '****abcd',
    })
    await wrapper.vm.$nextTick()
    expect(loadMock).toHaveBeenCalled()
    expect(wrapper.text()).toContain('已保存并生效')
  })

  it('保存失败时展示错误提示', async () => {
    saveSettingsFields.mockResolvedValueOnce({ ok: false, error: 'Base URL 格式错误' })
    const wrapper = mount(SettingsModels)
    const saveBtn = findBtn(wrapper, '保存')
    await saveBtn.trigger('click')
    await wrapper.vm.$nextTick()
    expect(wrapper.text()).toContain('保存失败')
    expect(loadMock).not.toHaveBeenCalled()
  })
})
