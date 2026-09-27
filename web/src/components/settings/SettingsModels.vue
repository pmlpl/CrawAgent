<script setup>
// 「对话模型」页签：多服务商模型注册表（列表/添加/编辑/删除/连通性测试）
// + 浏览器子 Agent 模型（032，驱动 browser_use_navigate 的小 Agent）
// Key 按服务商共享，后端不出明文（key_set 标记）；详细状态在 useSettings 组合式
import { computed, reactive, ref } from 'vue'
import { useSettings } from '../../composables/useSettings'

const { state, addModel, updateModel, deleteModel, test, saveSettingsFields, load } = useSettings()

// ---------- 浏览器子 Agent（032）：三键全空 = 跟随主模型；保存即生效无需重启 ----------
const savingSubAgent = ref(false)
const subAgentTip = ref(null)

async function saveBrowserAgent() {
  if (savingSubAgent.value) return
  savingSubAgent.value = true
  try {
    const res = await saveSettingsFields({
      browser_use_llm_model: state.browserAgent.model.trim(),
      browser_use_llm_base_url: state.browserAgent.baseUrl.trim(),
      browser_use_llm_api_key: state.browserAgent.apiKey,
    })
    subAgentTip.value = res.ok
      ? { ok: true, msg: '✓ 浏览器子 Agent 模型已保存并生效（无需重启）' }
      : { ok: false, msg: '保存失败：' + (res.error || '未知错误') }
    if (res.ok) await load() // 回读快照，把 key 回显成服务端脱敏掩码
  } finally {
    savingSubAgent.value = false
  }
}

// ---------- 服务商预设：选服务商自动填 Base URL，自定义可手填 ----------
const PROVIDER_PRESETS = [
  { value: 'DeepSeek', baseUrl: 'https://api.deepseek.com/v1' },
  { value: '通义千问', baseUrl: 'https://dashscope.aliyuncs.com/compatible-mode/v1' },
  { value: 'Moonshot', baseUrl: 'https://api.moonshot.cn/v1' },
  { value: 'OpenAI', baseUrl: 'https://api.openai.com/v1' },
  { value: '自定义', baseUrl: '' },
]

// ---------- 添加/编辑模型表单 ----------
const showForm = ref(false)
const editing = ref(null) // {provider, name}：null = 添加模式
const form = reactive({ provider: 'DeepSeek', model: '', apiKey: '', baseUrl: PROVIDER_PRESETS[0].baseUrl })
const showKey = ref(false)

const providerExists = () => state.providers.some(p => p.name === form.provider)
const keyPlaceholder = computed(() =>
  (editing.value || providerExists()) ? '留空 = 保留已配置的 Key' : 'sk-...')
const keyHint = computed(() => {
  if (providerExists()) return '该服务商已配置过 Key，留空即复用；填入新值则覆盖'
  return '同一服务商下的多个模型共用一个 Key'
})

function resetForm() {
  form.provider = 'DeepSeek'
  form.model = ''
  form.apiKey = ''
  form.baseUrl = PROVIDER_PRESETS[0].baseUrl
  showKey.value = false
  state.testResult = null
  state.saveTip = ''
}
function startAdd() {
  editing.value = null
  resetForm()
  showForm.value = true
}
function startEdit(m) {
  editing.value = { provider: m.provider, name: m.name }
  const p = state.providers.find(x => x.name === m.provider)
  form.provider = m.provider
  form.model = m.name
  form.apiKey = '' // 清空重填：留空保留原 Key
  form.baseUrl = p?.base_url || ''
  showKey.value = false
  state.testResult = null
  state.saveTip = ''
  showForm.value = true
}
function onProviderChange() {
  const preset = PROVIDER_PRESETS.find(x => x.value === form.provider)
  if (preset?.baseUrl) form.baseUrl = preset.baseUrl
}
async function submitForm() {
  const ok = editing.value
    ? await updateModel({ ...form, origProvider: editing.value.provider, origName: editing.value.name })
    : await addModel(form)
  if (ok) {
    showForm.value = false
    state.testResult = null
  }
}
async function remove(m) {
  if (!confirm(`删除模型 ${m.name}（${m.provider}）？`)) return
  await deleteModel(m.provider, m.name)
}
</script>

<template>
  <section class="card">
    <h2 class="card-title">模型</h2>
    <p v-if="state.loading" class="muted">加载中…</p>

    <template v-else>
      <!-- 模型列表 -->
      <div v-if="!showForm" class="field">
        <div class="list-head">
          <span class="label">已添加的模型</span>
          <button type="button" class="btn-primary" @click="startAdd">添加模型</button>
        </div>
        <table v-if="state.models.length" class="model-table">
          <thead>
            <tr>
              <th>模型名称</th>
              <th>服务商</th>
              <th class="th-actions">操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="m in state.models" :key="m.provider + '/' + m.name">
              <td class="mono">{{ m.name }}</td>
              <td>{{ m.provider }}</td>
              <td class="row-actions">
                <button type="button" class="icon-btn" aria-label="编辑" title="编辑" @click="startEdit(m)">
                  <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M17 3a2.85 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z" /></svg>
                </button>
                <button type="button" class="icon-btn danger" aria-label="删除" title="删除" @click="remove(m)">
                  <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 6h18" /><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6" /><path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" /></svg>
                </button>
              </td>
            </tr>
          </tbody>
        </table>
        <p v-else class="hint">还没有添加模型，点击右上角「添加模型」开始配置。</p>
      </div>

      <!-- 添加/编辑表单 -->
      <form v-else class="form" @submit.prevent="submitForm">
        <label class="field">
          <span class="label">服务商</span>
          <select v-model="form.provider" @change="onProviderChange">
            <option v-for="p in PROVIDER_PRESETS" :key="p.value" :value="p.value">{{ p.value }}</option>
          </select>
        </label>

        <label v-if="form.provider === '自定义'" class="field">
          <span class="label">Base URL</span>
          <input
            type="text"
            v-model="form.baseUrl"
            placeholder="https://..."
            autocomplete="off"
            spellcheck="false"
          />
        </label>

        <label class="field">
          <span class="label">模型 ID</span>
          <input
            type="text"
            v-model="form.model"
            placeholder="如 deepseek-chat"
            autocomplete="off"
            spellcheck="false"
          />
        </label>

        <label class="field">
          <span class="label">
            API Key
            <i v-if="providerExists()" class="badge">已配置</i>
          </span>
          <div class="input-row">
            <input
              :type="showKey ? 'text' : 'password'"
              v-model="form.apiKey"
              :placeholder="keyPlaceholder"
              autocomplete="off"
              spellcheck="false"
            />
            <button
              type="button"
              class="ghost"
              :aria-label="showKey ? '隐藏' : '显示'"
              @click="showKey = !showKey"
            >
              <svg v-if="!showKey" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8S1 12 1 12z" /><circle cx="12" cy="12" r="3" /></svg>
              <svg v-else width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24" /><line x1="1" y1="1" x2="23" y2="23" /></svg>
            </button>
          </div>
          <span class="hint">{{ keyHint }}</span>
        </label>

        <div v-if="state.testResult" class="result" :class="state.testResult.ok ? 'ok' : 'err'">
          {{ state.testResult.msg }}
        </div>
        <div v-if="state.saveTip" class="result err">{{ state.saveTip }}</div>

        <div class="actions">
          <button type="button" class="btn-ghost" @click="resetForm">重置</button>
          <button type="button" class="btn-ghost" :disabled="state.testing || !form.model" @click="test(form)">
            <span v-if="state.testing" class="spin" />测试连接
          </button>
          <button type="submit" class="btn-primary" :disabled="state.saving || !form.model">
            <span v-if="state.saving" class="spin" />{{ editing ? '保存修改' : '添加模型' }}
          </button>
          <button type="button" class="ghost" @click="showForm = false">取消</button>
        </div>
      </form>
    </template>
  </section>

  <section class="card">
    <h2 class="card-title">浏览器子 Agent</h2>
    <p class="hint" style="margin-bottom: 12px">
      驱动 browser_use_navigate 的浏览器小 Agent（登录 / 点击 / 填表等交互任务），浏览器操作用便宜快模型即可，重思考留给主 Agent。
      <b>三键全留空 = 跟随主模型</b>。须选支持图片输入的视觉模型（如 GLM-4V / GLM-5.1 视觉线），纯文本模型会退化为按 DOM 文本盲操作。
    </p>
    <div class="field" style="max-width: 420px">
      <label class="field">
        <span class="label">模型名</span>
        <input
          v-model="state.browserAgent.model"
          placeholder="留空 = 跟随主模型"
          spellcheck="false"
          autocomplete="off"
        />
      </label>
      <label class="field">
        <span class="label">Base URL</span>
        <input
          v-model="state.browserAgent.baseUrl"
          placeholder="留空 = 复用主服务商接口地址"
          spellcheck="false"
          autocomplete="off"
        />
      </label>
      <label class="field">
        <span class="label">API Key</span>
        <input
          type="password"
          v-model="state.browserAgent.apiKey"
          placeholder="未配置"
          spellcheck="false"
          autocomplete="off"
        />
        <span class="hint">已配置时回显脱敏掩码（****xxxx），含 * 视为掩码不回写；清空保存 = 清除。Base URL / Key 留空时回落主服务商对应值（只填模型名即可复用）</span>
      </label>
      <div class="actions" style="justify-content: flex-start">
        <button type="button" class="btn-primary" :disabled="savingSubAgent" @click="saveBrowserAgent">
          <span v-if="savingSubAgent" class="spin" />保存
        </button>
      </div>
      <div v-if="subAgentTip" class="result" :class="subAgentTip.ok ? 'ok' : 'err'" style="margin-top: 8px">
        {{ subAgentTip.msg }}
      </div>
    </div>
  </section>
</template>
