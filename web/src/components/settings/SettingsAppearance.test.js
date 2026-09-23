// SettingsAppearance 测试 — 界面外观页签：浏览器选择 + 背景图状态
import { describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'

// Mock useSettings（模块级单例，stub 最小可渲染状态）
vi.mock('../../composables/useSettings', () => ({
  useSettings: () => ({
    state: {
      startBrowser: '',
      saving: false,
      saveTip: '',
      saveTipOk: null,
    },
    saveStartBrowser: vi.fn(async () => true),
  }),
}))

// Mock useBg
vi.mock('../../composables/useBg', () => ({
  useBg: () => ({
    bgImage: { value: '' },
    bgOpacity: { value: 60 },
    setBg: vi.fn(async () => true),
    setBgOpacity: vi.fn(),
  }),
}))

import SettingsAppearance from './SettingsAppearance.vue'

describe('SettingsAppearance', () => {
  it('渲染浏览器选项', () => {
    const wrapper = mount(SettingsAppearance)
    // 应包含浏览器选项文本
    expect(wrapper.text()).toContain('Chrome')
    expect(wrapper.text()).toContain('Edge')
    expect(wrapper.text()).toContain('Firefox')
  })

  it('渲染背景图区域', () => {
    const wrapper = mount(SettingsAppearance)
    // 应有文件上传 input 或相关 UI
    expect(wrapper.find('input[type="file"]').exists()).toBe(true)
  })
})
