<script setup>
// 「对话模型」页签：多服务商模型注册表（列表/添加/编辑/删除/连通性测试）
// + 模型能力徽章与两维探测（033：视觉 + JSON 结构化输出）
// + 浏览器子 Agent 模型（032，驱动 browser_use_navigate 的小 Agent）
// Key 按服务商共享，后端不出明文（key_set 标记）；详细状态在 useSettings 组合式
import { computed, reactive, ref } from 'vue'
import { useSettings } from '../../composables/useSettings'

const { state, addModel, updateModel, deleteModel, test, saveSettingsFields, load,
        discoverModels, probeModel, saveManualVision } = useSettings()

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

// ---------- 能力发现与探测（033）----------
const discovered = ref([])       // 获取到的模型清单 [{id, owned_by}]
const discoverTip = ref(null)
const probeResult = ref(null)    // 最近一次探测结果（含模型原话）

// Base URL 合法 且（有 Key 可填 或 服务商已存 Key）才开放获取列表
const canDiscover = computed(() =>
  form.baseUrl.trim().startsWith('http') && (form.apiKey.trim().length > 0 || providerExists()))

async function discover() {
  discoverTip.value = null
  const res = await discoverModels({
    provider: providerExists() ? form.provider : '',
    apiKey: form.apiKey,
    baseUrl: form.baseUrl,
  })
  if (res.ok) {
    discovered.value = res.models || []
    discoverTip.value = discovered.value.length
      ? { ok: true, msg: `✓ 获取到 ${discovered.value.length} 个模型，在「模型 ID」输入框下拉点选回填` }
      : { ok: false, msg: '端点返回了空清单' }
  } else {
    discovered.value = []
    discoverTip.value = { ok: false, msg: res.error || '获取失败' }
  }
}

async function probe(m) {
  probeResult.value = null
  const res = await probeModel(m)
  probeResult.value = res.ok ? res : { model: m.name, error: res.error || '探测失败' }
}

async function toggleManualVision(m, checked) {
  await saveManualVision(m, checked)
}

// 徽章状态 → 样式类；true 过 / false 明确不过 / null 未验证
const capClass = (v) => (v === true ? 'cap-ok' : v === false ? 'cap-bad' : 'cap-unknown')
const visionLabel = (m) => {
  const c = m.caps || {}
  if (c.vision === true) {
    if (c.vision_source === 'probe') return '视觉 ✓'
    return c.vision_source === 'manual' ? '视觉 ✓手动' : '视觉 ✓推测'
  }
  return c.vision === false ? '视觉 ✗' : ''
}
const jsonLabel = (m) => {
  const c = m.caps || {}
  return c.structured === true ? 'JSON ✓' : c.structured === false ? 'JSON ✗' : ''
}

function resetForm() {
  form.provider = 'DeepSeek'
  form.model = ''
  form.apiKey = ''
  form.baseUrl = PROVIDER_PRESETS[0].baseUrl
  showKey.value = false
  state.testResult = null
  state.saveTip = ''
  discovered.value = []
  discoverTip.value = null
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
  discovered.value = []
  discoverTip.value = null
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

// ---------- 浏览器子 Agent（032/033）：三键全空 = 跟随主模型；保存即生效无需重启 ----------
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

// 子 Agent 模型下拉：两维全过排前，保留手输自定义
const CUSTOM = '__custom__'
const subAgentCustom = ref(false) // 选了"自定义"后置位（model 为空时保持选中态不弹回）
const poolSorted = computed(() => {
  const score = (m) => {
    const c = m.caps || {}
    if (c.vision === true && c.structured === true) return 0
    if (c.vision === true) return 1
    return 2
  }
  return [...state.models].sort((a, b) => score(a) - score(b))
})
const subAgentPick = computed(() => {
  if (subAgentCustom.value) return CUSTOM
  const v = state.browserAgent.model
  if (!v) return ''
  return poolSorted.value.some(m => m.name === v) ? v : CUSTOM
})
function onSubAgentPick(e) {
  const v = e.target.value
  if (v === CUSTOM) {
    subAgentCustom.value = true // 保持现值，交手输框编辑
  } else {
    subAgentCustom.value = false
    state.browserAgent.model = v
  }
}
const poolLabel = (m) => {
  const caps = [visionLabel(m), jsonLabel(m)].filter(Boolean).join(' ')
  return caps ? `${m.name}（${caps}）` : m.name
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
              <th>能力</th>
              <th class="th-actions">操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="m in state.models" :key="m.provider + '/' + m.name">
              <td class="mono">{{ m.name }}</td>
              <td>{{ m.provider }}</td>
              <td class="caps-cell">
                <span v-if="visionLabel(m)" class="cap" :class="capClass(m.caps?.vision)" :title="m.caps?.vision_reply || ''">{{ visionLabel(m) }}</span>
                <span v-if="jsonLabel(m)" class="cap" :class="capClass(m.caps?.structured)" :title="m.caps?.structured_reply || ''">{{ jsonLabel(m) }}</span>
                <label class="cap-manual" title="手动声明支持视觉（用户已知中转映射真相时勾选；探测复核会覆盖）">
                  <input type="checkbox" class="switch-input" :checked="m.caps?.vision_manual" @change="toggleManualVision(m, $event.target.checked)" />
                  <span class="switch-track"><span class="switch-knob" /></span>
                  <span class="cap-manual-text">手动</span>
                </label>
                <button
                  type="button"
                  class="btn-ghost btn-sm btn-busy"
                  :class="{ 'is-busy': state.probing === m.provider + '/' + m.name }"
                  :disabled="state.probing === m.provider + '/' + m.name"
                  title="两维探测：看图 + JSON 结构化输出（一次跑完）"
                  @click="probe(m)"
                >
                  <span class="btn-label">测试</span>
                  <span v-if="state.probing === m.provider + '/' + m.name" class="spin spin-center" />
                </button>
              </td>
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

        <!-- 最近一次探测结果（模型原话展示判定依据） -->
        <div v-if="probeResult" class="result" :class="probeResult.error ? 'err' : (probeResult.vision && probeResult.structured ? 'ok' : 'err')" style="margin-top: 10px">
          <template v-if="probeResult.error">{{ probeResult.model }}：{{ probeResult.error }}</template>
          <template v-else>
            <b class="mono">{{ probeResult.model }}</b> 探测结果：视觉 {{ probeResult.vision ? '✓' : '✗' }}（原话：{{ probeResult.vision_reply }}）；JSON 结构化 {{ probeResult.structured ? '✓' : '✗' }}（原话：{{ probeResult.structured_reply }}）
          </template>
        </div>
      </div>

      <!-- 添加/编辑表单 -->
      <form v-else class="form" @submit.prevent="submitForm">
        <label class="field">
          <span class="label">服务商</span>
          <select v-model="form.provider" @change="onProviderChange">
            <option v-for="p in PROVIDER_PRESETS" :key="p.value" :value="p.value">{{ p.value }}</option>
          </select>
        </label>

        <label class="field">
          <span class="label">Base URL</span>
          <input
            type="text"
            v-model="form.baseUrl"
            placeholder="https://...（OpenAI 兼容端点，一般以 /v1 结尾）"
            autocomplete="off"
            spellcheck="false"
          />
        </label>

        <div class="field">
          <span class="label">模型 ID</span>
          <div class="input-row">
            <input
              type="text"
              v-model="form.model"
              list="discovered-model-list"
              placeholder="如 deepseek-chat"
              autocomplete="off"
              spellcheck="false"
            />
            <button
              v-if="canDiscover"
              type="button"
              class="ghost btn-busy"
              :class="{ 'is-busy': state.discovering }"
              :disabled="state.discovering"
              @click="discover"
            >
              <span class="btn-label">获取模型列表</span>
              <span v-if="state.discovering" class="spin spin-center" />
            </button>
          </div>
          <datalist id="discovered-model-list">
            <option v-for="d in discovered" :key="d.id" :value="d.id">{{ d.owned_by }}</option>
          </datalist>
          <span v-if="discoverTip" class="hint" :class="{ 'discover-err': !discoverTip.ok }">{{ discoverTip.msg }}</span>
          <span v-else class="hint">填写 Base URL 与 Key 后可一键拉取该端点的全部模型，下拉点选回填</span>
        </div>

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
          <button type="button" class="btn-ghost btn-busy" :class="{ 'is-busy': state.testing }" :disabled="state.testing || !form.model" @click="test(form)">
            <span class="btn-label">测试连接</span>
            <span v-if="state.testing" class="spin spin-center" />
          </button>
          <button type="submit" class="btn-primary btn-busy" :class="{ 'is-busy': state.saving }" :disabled="state.saving || !form.model">
            <span class="btn-label">{{ editing ? '保存修改' : '添加模型' }}</span>
            <span v-if="state.saving" class="spin spin-center" />
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
      <b>三键全留空 = 跟随主模型</b>（不手配时自动挑池中两维全过的视觉模型，见模型表「能力」列）。
    </p>
    <div class="field" style="max-width: 480px">
      <label class="field">
        <span class="label">模型名</span>
        <select :value="subAgentPick" @change="onSubAgentPick" aria-label="浏览器子 Agent 模型">
          <option value="">跟随主模型（留空，不手配时自动挑两维全过模型）</option>
          <option v-for="m in poolSorted" :key="m.provider + '/' + m.name" :value="m.name">{{ poolLabel(m) }}</option>
          <option :value="CUSTOM">自定义模型名…</option>
        </select>
        <input
          v-if="subAgentPick === CUSTOM"
          v-model="state.browserAgent.model"
          placeholder="输入模型 ID"
          spellcheck="false"
          autocomplete="off"
          style="margin-top: 8px"
        />
        <span class="hint">下拉按能力排序：两维全过（视觉 ✓ JSON ✓）→ 仅视觉过 → 其他；须选支持图片输入的视觉模型，纯文本模型会退化为按 DOM 文本盲操作</span>
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
        <button type="button" class="btn-primary btn-busy" :class="{ 'is-busy': savingSubAgent }" :disabled="savingSubAgent" @click="saveBrowserAgent">
          <span class="btn-label">保存</span>
          <span v-if="savingSubAgent" class="spin spin-center" />
        </button>
      </div>
      <div v-if="subAgentTip" class="result" :class="subAgentTip.ok ? 'ok' : 'err'" style="margin-top: 8px">
        {{ subAgentTip.msg }}
      </div>
    </div>
  </section>
</template>

<style scoped>
/* 能力徽章（033）：绿=通过、红=明确不过；未验证不显示徽章 */
.caps-cell {
  white-space: nowrap;
}
.cap {
  display: inline-block;
  font-size: 11px;
  line-height: 1;
  padding: 3px 7px;
  margin-right: 6px;
  border-radius: 9px;
  border: 1px solid var(--line);
  white-space: nowrap;
}
.cap-ok {
  color: var(--accent);
  background: var(--accent-soft);
  border-color: color-mix(in srgb, var(--accent) 35%, transparent);
}
.cap-bad {
  color: var(--danger, #e5484d);
  border-color: color-mix(in srgb, var(--danger, #e5484d) 35%, transparent);
}
/* loading 按钮：spinner 绝对定位居中覆盖、文字藏形保占 —— 按钮尺寸不因 loading 变化 */
.btn-busy {
  position: relative;
}
.btn-busy.is-busy .btn-label {
  visibility: hidden;
}
.btn-busy .spin-center {
  position: absolute;
  left: 50%;
  top: 50%;
  margin: -6.5px 0 0 -6.5px;
}

/* 视觉手动勾选：迷你滑动开关（沿用全局滑动 switch 偏好） */
.cap-manual {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  margin-right: 6px;
  cursor: pointer;
  vertical-align: middle;
  position: relative;
}
.cap-manual-text {
  font-size: 11px;
  color: var(--dim);
}
.switch-input {
  position: absolute;
  opacity: 0;
  width: 0;
  height: 0;
}
.switch-track {
  width: 28px;
  height: 16px;
  border-radius: 8px;
  background: rgba(128, 128, 128, 0.35);
  position: relative;
  transition: background 0.2s;
  flex-shrink: 0;
}
.switch-knob {
  position: absolute;
  top: 2px;
  left: 2px;
  width: 12px;
  height: 12px;
  border-radius: 50%;
  background: #fff;
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.3);
  transition: transform 0.2s;
}
.switch-input:checked + .switch-track {
  background: var(--accent);
}
.switch-input:checked + .switch-track .switch-knob {
  transform: translateX(12px);
}
.switch-input:focus-visible + .switch-track {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
}

.discover-err {
  color: var(--danger, #e5484d);
}
</style>
