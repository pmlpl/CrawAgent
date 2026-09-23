// useBg 测试 — 背景图单例：服务端存储 + CSS 应用 + 浓度调节
import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest'

describe('useBg', () => {
  beforeEach(() => {
    vi.resetModules()
    // useBg 初始化时调 fetchState() → fetch('/api/background')，stub 一个空响应
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: false, json: async () => ({}) })))
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('初始状态：空背景 + 默认浓度 60', async () => {
    const { useBg } = await import('./useBg.js')
    const { bgImage, bgOpacity } = useBg()
    expect(bgImage.value).toBe('')
    expect(bgOpacity.value).toBe(60)
  })

  it('setBg(dataUrl) → POST /api/background + 切换到服务端 URL', async () => {
    const mockFetch = vi.fn(async (url, opts) => {
      if (url === '/api/background' && opts?.method === 'POST') {
        return { ok: true, json: async () => ({ image: '/api/background/image?t=999' }) }
      }
      return { ok: false, json: async () => ({}) }
    })
    vi.stubGlobal('fetch', mockFetch)
    const { useBg } = await import('./useBg.js')
    const { bgImage, setBg } = useBg()
    const ok = await setBg('data:image/jpeg;base64,abc')
    expect(ok).toBe(true)
    expect(bgImage.value).toBe('/api/background/image?t=999')
  })

  it('setBg("") → DELETE /api/background + 清空背景', async () => {
    const mockFetch = vi.fn(async (url, opts) => {
      if (url === '/api/background' && opts?.method === 'DELETE') {
        return { ok: true, json: async () => ({}) }
      }
      return { ok: false, json: async () => ({}) }
    })
    vi.stubGlobal('fetch', mockFetch)
    const { useBg } = await import('./useBg.js')
    const { bgImage, setBg } = useBg()
    // 先设一个值再清除
    bgImage.value = 'some-url'
    await setBg('')
    expect(bgImage.value).toBe('')
  })

  it('setBgOpacity 钳位到 0-95', async () => {
    const { useBg } = await import('./useBg.js')
    const { bgOpacity, setBgOpacity } = useBg()
    setBgOpacity(150)
    expect(bgOpacity.value).toBe(95)
    setBgOpacity(-10)
    expect(bgOpacity.value).toBe(0)
    setBgOpacity(42)
    expect(bgOpacity.value).toBe(42)
  })

  it('setBgOpacity 非数字被忽略', async () => {
    const { useBg } = await import('./useBg.js')
    const { bgOpacity, setBgOpacity } = useBg()
    const before = bgOpacity.value
    setBgOpacity('not-a-number')
    expect(bgOpacity.value).toBe(before)
  })

  it('服务端保存失败返回 false', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: false, json: async () => ({}) })))
    const { useBg } = await import('./useBg.js')
    const { setBg } = useBg()
    const ok = await setBg('data:image/jpeg;base64,xyz')
    expect(ok).toBe(false)
  })
})
