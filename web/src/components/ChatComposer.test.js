// ChatComposer 测试 — 输入区一排两按钮（035 选择项目 / 036 附件上传链路）
// useChat / useSettings 用可变 stub 顶替，测试直接摆状态后 mount。
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'

vi.mock('../composables/useChat', () => {
  const state = {
    session: 'sess-test',
    busy: false,
    lastStatus: '',
    draft: '',
    workDir: '',
    attachments: [],
    chooseProject: vi.fn(),
    notifyError: vi.fn(),
    addAttachment: vi.fn(),
    removeAttachment: vi.fn(),
    clearAttachments: vi.fn(),
    send: vi.fn(),
  }
  globalThis.__composerChat = state
  return { useChat: () => state }
})

vi.mock('../composables/useSettings', () => ({
  useSettings: () => ({
    state: { models: [], model: 'test-model', thinkingDepth: 'off' },
    saveThinking: vi.fn(),
    setSelectedModel: vi.fn(),
  }),
}))

import ChatComposer from './ChatComposer.vue'

function st() { return globalThis.__composerChat }

function freshState(overrides = {}) {
  const s = st()
  s.session = 'sess-test'
  s.busy = false
  s.lastStatus = ''
  s.workDir = ''
  s.attachments = []
  for (const k of ['chooseProject', 'notifyError', 'addAttachment', 'removeAttachment']) s[k].mockClear()
  Object.assign(s, overrides)
  return s
}

beforeEach(() => {
  freshState()
  vi.stubGlobal('fetch', vi.fn(async () => ({
    ok: true,
    json: async () => ({ ok: true, name: 'a.md', path: 'D:/uploads/sess-test/a.md', size: 3 }),
  })))
})

function mountComposer(props = {}) {
  return mount(ChatComposer, { props: { busy: false, restore: '', ...props } })
}

describe('ChatComposer 输入区按钮（035 + 036）', () => {
  it('附件按钮与「选择项目」按钮同排，选择项目在附件右侧', () => {
    const w = mountComposer()
    const btns = w.findAll('.tool-group.left .tool-btn')
    expect(btns.length).toBe(2)
    expect(btns[0].attributes('aria-label')).toBe('添加附件')
    expect(btns[1].text()).toContain('选择项目') // 未绑定态显示文案
  })

  it('点「选择项目」→ 调 chooseProject（选框 + 落库二段式）', async () => {
    const w = mountComposer()
    await w.findAll('.tool-group.left .tool-btn')[1].trigger('click')
    expect(st().chooseProject).toHaveBeenCalledTimes(1)
  })

  it('已绑定项目 → 按钮高亮并显示路径（点击=更换）', () => {
    freshState({ workDir: 'D:/我的项目' })
    const w = mountComposer()
    const btn = w.findAll('.tool-group.left .tool-btn')[1]
    expect(btn.classes()).toContain('active')
    expect(btn.find('.project-path').text()).toBe('D:/我的项目')
    expect(btn.attributes('title')).toContain('点击更换')
  })

  it('busy 时两个按钮都禁用（防中途改清单）', () => {
    const w = mountComposer({ busy: true })
    const btns = w.findAll('.tool-group.left .tool-btn')
    expect(btns[0].attributes('disabled')).toBeDefined()
    expect(btns[1].attributes('disabled')).toBeDefined()
  })

  it('点附件按钮 → 触发隐藏 file input 的 click', async () => {
    const w = mountComposer()
    const input = w.find('input[type="file"]')
    expect(input.exists()).toBe(true)
    const spy = vi.spyOn(input.element, 'click')
    await w.findAll('.tool-group.left .tool-btn')[0].trigger('click')
    expect(spy).toHaveBeenCalled()
  })

  it('选文件 → POST /api/uploads（带 session_id）→ 成功进附件清单', async () => {
    const w = mountComposer()
    const input = w.find('input[type="file"]')
    Object.defineProperty(input.element, 'files', {
      value: [new File(['abc'], 'a.md', { type: 'text/markdown' })],
      configurable: true,
    })
    await input.trigger('change')
    await flushPromises()

    expect(fetch).toHaveBeenCalledTimes(1)
    const [url, opts] = fetch.mock.calls[0]
    expect(url).toBe('/api/uploads')
    expect(opts.body).toBeInstanceOf(FormData)
    expect(opts.body.get('session_id')).toBe('sess-test')
    expect(opts.body.get('file').name).toBe('a.md')
    expect(st().addAttachment).toHaveBeenCalledWith({ name: 'a.md', path: 'D:/uploads/sess-test/a.md', size: 3 })
  })

  it('上传失败（超限等）→ 聊天区错误条人话提示', async () => {
    fetch.mockImplementation(async () => ({
      ok: true,
      json: async () => ({ ok: false, error: '文件超过 50MB 上限' }),
    }))
    const w = mountComposer()
    const input = w.find('input[type="file"]')
    Object.defineProperty(input.element, 'files', {
      value: [new File(['x'], 'big.bin')],
      configurable: true,
    })
    await input.trigger('change')
    await flushPromises()

    expect(st().addAttachment).not.toHaveBeenCalled()
    expect(st().notifyError).toHaveBeenCalledTimes(1)
    expect(st().notifyError.mock.calls[0][0]).toContain('50MB')
  })

  it('附件 chip 列表：文件名 + 大小渲染，× 移除', async () => {
    freshState({ attachments: [{ name: '笔记.txt', path: 'D:/u/笔记.txt', size: 2048 }] })
    const w = mountComposer()
    const chip = w.find('.attach-chips .chip')
    expect(chip.exists()).toBe(true)
    expect(chip.text()).toContain('笔记.txt')
    expect(chip.text()).toContain('2.0 KB')
    await chip.find('.chip-x').trigger('click')
    expect(st().removeAttachment).toHaveBeenCalledWith(0)
  })
})
