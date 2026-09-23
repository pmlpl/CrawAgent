// useTheme 测试 — 主题单例：dark/light 切换 + localStorage 持久化
import { beforeEach, describe, expect, it, vi } from 'vitest'

describe('useTheme', () => {
  beforeEach(() => {
    localStorage.clear()
    delete document.documentElement.dataset.theme
    vi.resetModules()
  })

  it('默认暗色主题', async () => {
    const { useTheme } = await import('./useTheme.js')
    const { theme } = useTheme()
    expect(theme.value).toBe('dark')
    expect(document.documentElement.dataset.theme).toBe('dark')
  })

  it('toggle 切换到亮色并持久化', async () => {
    const { useTheme } = await import('./useTheme.js')
    const { theme, toggle } = useTheme()
    toggle()
    expect(theme.value).toBe('light')
    expect(document.documentElement.dataset.theme).toBe('light')
    expect(localStorage.getItem('crawagent-theme')).toBe('light')
  })

  it('toggle 二次切回暗色', async () => {
    const { useTheme } = await import('./useTheme.js')
    const { theme, toggle } = useTheme()
    toggle() // dark → light
    toggle() // light → dark
    expect(theme.value).toBe('dark')
    expect(localStorage.getItem('crawagent-theme')).toBe('dark')
  })

  it('初始化时恢复已保存的亮色主题', async () => {
    localStorage.setItem('crawagent-theme', 'light')
    const { useTheme } = await import('./useTheme.js')
    const { theme } = useTheme()
    expect(theme.value).toBe('light')
    expect(document.documentElement.dataset.theme).toBe('light')
  })

  it('无效的 saved 值回退到暗色', async () => {
    localStorage.setItem('crawagent-theme', 'purple')
    const { useTheme } = await import('./useTheme.js')
    const { theme } = useTheme()
    expect(theme.value).toBe('dark')
  })
})
