<script setup>
// 「对话模型」页签：多服务商模型注册表（列表/添加/编辑/删除/连通性测试）
// Key 按服务商共享，后端不出明文（key_set 标记）；详细状态在 useSettings 组合式
import { computed, reactive, ref } from 'vue'
import { useSettings } from '../../composables/useSettings'

const { state, addModel, updateModel, deleteModel, test } = useSettings()

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
</template>
