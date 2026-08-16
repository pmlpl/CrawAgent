// 设置面板状态：读取、测试、保存 API Key / 模型 / Base URL
import { reactive, ref } from 'vue'

const state = reactive({
  open: false,
  loading: false,
  saving: false,
  testing: false,
  apiKey: '',
  apiKeySet: false,         // 是否在服务端已配置（用于只显示掩码）
  baseUrl: '',
  model: '',
  testResult: null,         // {ok: bool, response/error: string}
  saveTip: '',              // 保存提示文案
})

async function load() {
  state.loading = true
  try {
    const res = await fetch('/api/settings')
    if (!res.ok) return
    const data = await res.json()
    state.apiKey = data.openai_api_key || ''
    state.apiKeySet = !!data.openai_api_key_set
    state.baseUrl = data.openai_base_url || ''
    state.model = data.default_model || ''
  } finally {
    state.loading = false
  }
}

async function save() {
  state.saving = true
  state.testResult = null
  state.saveTip = ''
  try {
    const body = {
      openai_base_url: state.baseUrl.trim(),
      default_model: state.model.trim(),
    }
    // API Key：只在用户改了（不再是掩码，掩码含星号）时下发
    if (state.apiKey && !state.apiKey.includes('*')) {
      body.openai_api_key = state.apiKey.trim()
    }
    const res = await fetch('/api/settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    const data = await res.json()
    if (data.ok) {
      state.saveTip = '已保存，下次对话生效'
      // 重新拉一次以更新 apiKeySet 与掩码回显
      await load()
    } else {
      state.saveTip = '保存失败：' + (data.error || '未知错误')
    }
  } catch (e) {
    state.saveTip = '保存失败：' + (e?.message || e)
  } finally {
    state.saving = false
  }
}

async function test() {
  state.testing = true
  state.testResult = null
  state.saveTip = ''
  try {
    const body = {
      openai_base_url: state.baseUrl.trim(),
      default_model: state.model.trim(),
    }
    if (state.apiKey && !state.apiKey.includes('*')) {
      body.openai_api_key = state.apiKey.trim()
    }
    const res = await fetch('/api/settings/test', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    const data = await res.json()
    state.testResult = data.ok
      ? { ok: true, msg: '✓ 连接成功 · ' + (data.response || '') }
      : { ok: false, msg: '✗ ' + (data.error || '失败') }
  } catch (e) {
    state.testResult = { ok: false, msg: '✗ ' + (e?.message || e) }
  } finally {
    state.testing = false
  }
}

async function open() {
  state.open = true
  state.testResult = null
  state.saveTip = ''
  await load()
}

function close() {
  state.open = false
}

export function useSettings() {
  return { state, load, save, test, open, close }
}
