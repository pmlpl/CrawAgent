// useSettings 测试 — 设置面板状态层：模型 CRUD + 快照加载 + 模型选择持久化
import { beforeEach, describe, expect, it, vi } from 'vitest'

// 辅助：构造 /api/settings 响应
function _settingsResponse(overrides = {}) {
  return {
    models: [{ name: 'gpt-4', provider: 'OpenAI' }],
    providers: [{ name: 'OpenAI', base_url: 'https://api.openai.com/v1', key_set: true }],
    thinking_depth: 'off',
    start_browser: '',
    request_timeout: 30,
    request_delay: 1.0,
    output_dir: '/tmp/output',
    downloads_dir: '/tmp/downloads',
    ...overrides,
  }
}

describe('useSettings', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.resetModules()
  })

  describe('初始状态', () => {
    it('models/providers 为空数组', async () => {
      vi.stubGlobal('fetch', vi.fn(async () => ({ ok: false, json: async () => ({}) })))
      const { useSettings } = await import('./useSettings.js')
      const { state } = useSettings()
      expect(state.models).toEqual([])
      expect(state.providers).toEqual([])
      expect(state.open).toBe(false)
    })

    it('config.defaultModel 为空字符串（无模型时）', async () => {
      vi.stubGlobal('fetch', vi.fn(async () => ({ ok: false, json: async () => ({}) })))
      const { useSettings } = await import('./useSettings.js')
      const { config } = useSettings()
      expect(config.value.defaultModel).toBe('')
    })
  })

  describe('load()', () => {
    it('从 /api/settings 加载快照', async () => {
      vi.stubGlobal('fetch', vi.fn(async () => ({
        ok: true,
        json: async () => _settingsResponse(),
      })))
      const { useSettings } = await import('./useSettings.js')
      const { state, load } = useSettings()
      await load()
      expect(state.models.length).toBe(1)
      expect(state.models[0].name).toBe('gpt-4')
      expect(state.providers[0].name).toBe('OpenAI')
      expect(state.thinkingDepth).toBe('off')
    })

    it('字符串条目归一化为 {name, provider}', async () => {
      vi.stubGlobal('fetch', vi.fn(async () => ({
        ok: true,
        json: async () => ({ models: ['legacy-model', { name: 'new', provider: 'X' }] }),
      })))
      const { useSettings } = await import('./useSettings.js')
      const { state, load } = useSettings()
      await load()
      expect(state.models.length).toBe(2)
      expect(state.models[0]).toEqual({ name: 'legacy-model', provider: '' })
      expect(state.models[1]).toEqual({ name: 'new', provider: 'X' })
    })

    it('无效条目被过滤', async () => {
      vi.stubGlobal('fetch', vi.fn(async () => ({
        ok: true,
        json: async () => ({ models: [{ name: 'ok' }, null, { provider: 'x' }, 'str'] }),
      })))
      const { useSettings } = await import('./useSettings.js')
      const { state, load } = useSettings()
      await load()
      expect(state.models.length).toBe(2) // {name:'ok'} + 'str'→{name:'str',provider:''}
    })

    it('高级页字段正确映射', async () => {
      vi.stubGlobal('fetch', vi.fn(async () => ({
        ok: true,
        json: async () => _settingsResponse({
          request_timeout: 60,
          request_delay: 2.5,
          output_dir: '/data/output',
          downloads_dir: '/data/dl',
        }),
      })))
      const { useSettings } = await import('./useSettings.js')
      const { state, load } = useSettings()
      await load()
      expect(state.adv.timeout).toBe(60)
      expect(state.adv.delay).toBe(2.5)
      expect(state.adv.outputDir).toBe('/data/output')
      expect(state.adv.downloadsDir).toBe('/data/dl')
    })
  })

  describe('模型选择持久化', () => {
    it('localStorage 有已选模型且在列表中 → 恢复选择', async () => {
      localStorage.setItem('crawagent-selected-model', 'gpt-4')
      vi.stubGlobal('fetch', vi.fn(async () => ({
        ok: true,
        json: async () => _settingsResponse(),
      })))
      const { useSettings } = await import('./useSettings.js')
      const { state, load } = useSettings()
      await load()
      expect(state.model).toBe('gpt-4')
    })

    it('localStorage 模型不在列表中 → 回退到第一个', async () => {
      localStorage.setItem('crawagent-selected-model', 'deleted-model')
      vi.stubGlobal('fetch', vi.fn(async () => ({
        ok: true,
        json: async () => _settingsResponse(),
      })))
      const { useSettings } = await import('./useSettings.js')
      const { state, load } = useSettings()
      await load()
      expect(state.model).toBe('gpt-4')
    })

    it('setSelectedModel 持久化到 localStorage', async () => {
      vi.stubGlobal('fetch', vi.fn(async () => ({ ok: false, json: async () => ({}) })))
      const { useSettings } = await import('./useSettings.js')
      const { state, setSelectedModel } = useSettings()
      setSelectedModel('claude-3')
      expect(state.model).toBe('claude-3')
      expect(localStorage.getItem('crawagent-selected-model')).toBe('claude-3')
    })

    it('setSelectedModel 空值被忽略', async () => {
      vi.stubGlobal('fetch', vi.fn(async () => ({ ok: false, json: async () => ({}) })))
      const { useSettings } = await import('./useSettings.js')
      const { state, setSelectedModel } = useSettings()
      state.model = 'keep-me'
      setSelectedModel('')
      expect(state.model).toBe('keep-me')
    })
  })

  describe('addModel', () => {
    it('成功 → 快照更新', async () => {
      const mockFetch = vi.fn(async (url) => ({
        ok: true,
        json: async () => {
          if (url === '/api/models') {
            return { ok: true, models: [{ name: 'new-model', provider: 'Test' }] }
          }
          return {}
        },
      }))
      vi.stubGlobal('fetch', mockFetch)
      const { useSettings } = await import('./useSettings.js')
      const { state, addModel } = useSettings()
      const ok = await addModel({ provider: 'Test', model: 'new-model', apiKey: 'k', baseUrl: 'http://x' })
      expect(ok).toBe(true)
      expect(state.models.some(m => m.name === 'new-model')).toBe(true)
    })

    it('后端返回 error → 提示 + 返回 false', async () => {
      vi.stubGlobal('fetch', vi.fn(async () => ({
        ok: true,
        json: async () => ({ ok: false, error: '模型已存在' }),
      })))
      const { useSettings } = await import('./useSettings.js')
      const { state, addModel } = useSettings()
      const ok = await addModel({ provider: 'X', model: 'dup', apiKey: 'k', baseUrl: '' })
      expect(ok).toBe(false)
      expect(state.saveTip).toContain('模型已存在')
      expect(state.saveTipOk).toBe(false)
    })
  })

  describe('deleteModel', () => {
    it('成功 → 快照更新', async () => {
      vi.stubGlobal('fetch', vi.fn(async (url) => ({
        ok: true,
        json: async () => {
          if (url === '/api/models/delete') {
            return { ok: true, models: [] }
          }
          return {}
        },
      })))
      const { useSettings } = await import('./useSettings.js')
      const { state, deleteModel } = useSettings()
      // 先加载初始数据
      state.models = [{ name: 'to-delete', provider: 'X' }]
      const ok = await deleteModel('X', 'to-delete')
      expect(ok).toBe(true)
      expect(state.models.length).toBe(0)
    })
  })

  describe('open/close', () => {
    it('open → state.open = true 并触发 load', async () => {
      const mockFetch = vi.fn(async () => ({
        ok: true,
        json: async () => _settingsResponse(),
      }))
      vi.stubGlobal('fetch', mockFetch)
      const { useSettings } = await import('./useSettings.js')
      const { state, open } = useSettings()
      await open()
      expect(state.open).toBe(true)
      expect(mockFetch).toHaveBeenCalled()
    })

    it('close → state.open = false', async () => {
      vi.stubGlobal('fetch', vi.fn(async () => ({ ok: false, json: async () => ({}) })))
      const { useSettings } = await import('./useSettings.js')
      const { state, close } = useSettings()
      state.open = true
      close()
      expect(state.open).toBe(false)
    })
  })
})
