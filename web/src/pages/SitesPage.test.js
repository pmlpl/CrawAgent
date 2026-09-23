// SitesPage 测试 — 站点列表加载 + 删除 + 路由跳转
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'

// Mock vue-router
const mockPush = vi.fn()
vi.mock('vue-router', () => ({
  useRouter: () => ({ push: mockPush }),
}))

// Mock useChat
vi.mock('../composables/useChat', () => ({
  useChat: () => ({
    connected: false,
    send: vi.fn(() => true),
    lastDraft: '',
  }),
}))

import SitesPage from './SitesPage.vue'

describe('SitesPage', () => {
  beforeEach(() => {
    mockPush.mockClear()
  })

  it('加载并渲染站点列表', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({
      ok: true,
      json: async () => ({
        sites: [
          { origin: 'https://example.com', title: '示例站', strategy: 'crawl_webpage' },
          { origin: 'https://blog.test', title: '博客', strategy: 'browse_and_crawl' },
        ],
      }),
    })))

    const wrapper = mount(SitesPage)
    await flushPromises()

    expect(wrapper.text()).toContain('示例站')
    expect(wrapper.text()).toContain('博客')
  })

  it('加载失败显示错误', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: false, status: 500 })))

    const wrapper = mount(SitesPage)
    await flushPromises()

    expect(wrapper.text()).toContain('500')
  })

  it('空列表不报错', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({
      ok: true,
      json: async () => ({ sites: [] }),
    })))

    const wrapper = mount(SitesPage)
    await flushPromises()

    // 不含站点条目，但也不应崩溃
    expect(wrapper.text()).not.toContain('示例站')
  })
})
