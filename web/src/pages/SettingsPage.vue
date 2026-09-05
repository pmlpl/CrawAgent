<script setup>
// 设置页：多服务商模型注册表（服务商/模型/API Key）、思考深度、界面偏好
// 站点级凭据（如微信读书 Cookie）不在这里——它们属于站点档案，
// 在「站点档案」页按站点配置。
// 背景图上传：用户自选图片作为聊天背景板，仅本地保存
import { computed, onMounted, reactive, ref } from 'vue'
import { useSettings } from '../composables/useSettings'
import { useBg } from '../composables/useBg'

const { state, load, addModel, updateModel, deleteModel, test, saveMcp, startAA } = useSettings()

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

// ---------- 背景图（useBg 组合式：localStorage 持久化，App.vue 启动时恢复） ----------
const { bgImage, bgOpacity, setBg, setBgOpacity } = useBg()
const bgFile = ref(null)
const bgUploading = ref(false)

function onOpacityInput(e) {
  setBgOpacity(e.target.value)
}

function onBgPick(e) {
  const f = e.target.files?.[0]
  if (!f) return
  if (!f.type.startsWith('image/')) {
    bgUploading.value = false
    return
  }
  bgUploading.value = true
  const reader = new FileReader()
  reader.onload = () => {
    setBg(String(reader.result || ''))
    bgUploading.value = false
  }
  reader.onerror = () => { bgUploading.value = false }
  reader.readAsDataURL(f)
}
function clearBg() {
  setBg('')
  if (bgFile.value) bgFile.value.value = ''
}

// 思考深度已不在设置页配置——对话页输入栏的思考菜单可直接切换（存后端 .env）

onMounted(async () => {
  await load()
})
</script>

<template>
  <div class="page">
    <div class="page-head">
      <h1>设置</h1>
      <p class="muted">API 凭据与界面偏好</p>
    </div>

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
      <h2 class="card-title">界面</h2>

      <div class="field">
        <span class="label">背景图</span>
        <p class="hint">上传一张图片作为聊天页背景板（仅本地保存，不上传服务器）</p>
        <div class="bg-row">
          <input
            ref="bgFile"
            type="file"
            accept="image/*"
            class="file-input"
            @change="onBgPick"
          />
          <button v-if="bgImage" type="button" class="btn-ghost" @click="clearBg">清除背景</button>
        </div>
        <div v-if="bgUploading" class="hint">处理中…</div>
        <div v-if="bgImage" class="bg-preview" :style="{ backgroundImage: `url(${bgImage})` }" />
        <div v-if="bgImage" class="bg-opacity-row">
          <span class="label">遮罩浓度 <b class="mono">{{ bgOpacity }}%</b></span>
          <input
            type="range"
            min="0"
            max="95"
            step="5"
            :value="bgOpacity"
            aria-label="遮罩浓度"
            @input="onOpacityInput"
          />
          <p class="hint">越高文字越清晰、背景越淡。半透明卡片（用户气泡/轨迹/报错条）在背景开启时已自动换成实底，不会透字</p>
        </div>
      </div>
    </section>

    <section class="card">
      <h2 class="card-title">MCP 服务</h2>

      <div class="field">
        <div class="bg-row">
          <span class="label">自动启动 MCP 服务</span>
          <button
            type="button"
            class="btn-ghost"
            :disabled="!state.mcpConfigured"
            :style="state.mcpAutostart ? 'color: var(--accent); border-color: var(--accent)' : ''"
            @click="state.mcpAutostart = !state.mcpAutostart"
          >
            {{ state.mcpAutostart ? 'ON' : 'OFF' }}
          </button>
        </div>
        <p class="hint" v-if="!state.mcpConfigured">
          未配置 MCP_SERVERS（.env），开关无效。配置示例：
          [{"name":"anything","transport":"streamable_http","url":"http://127.0.0.1:23816/mcp","headers":{"Authorization":"Bearer xxx"}}]
        </p>
        <p class="hint" v-else-if="state.aaBuiltinFound">
          打开后：Agent 检测到 anything-analyzer 未运行时，会自动在后台启动 Electron 应用（pnpm dev），无需手动开终端
        </p>
        <p class="hint" v-else>
          打开后：Agent 检测到 MCP 服务未运行时，会执行下方自定义命令拉起服务
        </p>

        <!-- 内置 anything-analyzer 状态展示 -->
        <div v-if="state.mcpConfigured && state.aaBuiltinFound" class="builtin-hint">
          <span class="builtin-dot">✓</span>
          <span>已内置 anything-analyzer 启动支持</span>
          <span class="builtin-path">{{ state.aaBuiltinPath }}</span>
        </div>
        <div v-else-if="state.mcpConfigured && !state.aaBuiltinFound && state.aaBuiltinPort === 23816" class="builtin-hint warn">
          <span class="builtin-dot">!</span>
          <span>没找到 anything-analyzer 项目路径</span>
          <span class="builtin-path">请在 .env 里加 ANYTHING_ANALYZER_PATH=你的项目路径</span>
        </div>

        <!-- 启动脚本提示 -->
        <div v-if="state.mcpConfigured" class="builtin-hint" style="margin-top:10px">
          <span class="builtin-dot">ⓘ</span>
          <span>首次启动请双击运行：</span>
          <code class="mono">crawagent\scripts\start_anything_analyzer.bat</code>
          <span>（或拖到浏览器窗口外手动双击）</span>
        </div>
        <button
          v-if="state.mcpConfigured"
          type="button"
          class="btn-primary"
          :disabled="state.saving"
          style="margin-top: 8px"
          @click="startAA"
        >
          <span v-if="state.saving" class="spin" />📂 打开启动脚本文件夹
        </button>
        <p class="hint" v-if="state.saveTip" style="margin-top: 6px">{{ state.saveTip }}</p>

        <!-- 高级模式：自定义命令 -->
        <div v-if="state.mcpConfigured" class="field">
          <button type="button" class="btn-ghost ghost-link" @click="state.advancedMode = !state.advancedMode">
            {{ state.advancedMode ? '收起自定义命令 ▲' : '高级：自定义启动命令 ▼' }}
          </button>
          <textarea
            v-if="state.advancedMode"
            v-model="state.mcpStartCommand"
            rows="2"
            class="mcp-cmd"
            placeholder="留空 = 用内置 anything-analyzer 启动；填入 = 用你自己的命令"
          />
        </div>

        <button
          v-if="state.mcpConfigured"
          type="button"
          class="btn-ghost"
          :disabled="state.saving"
          @click="saveMcp"
        >
          {{ state.saving ? '启动中…' : '保存并启动' }}
        </button>
        <p class="hint" v-if="state.saveTip">{{ state.saveTip }}</p>
      </div>
    </section>

    <section class="card card-ghost">
      <h2 class="card-title muted">更多</h2>
      <p class="muted">后续可在此扩展：站点 Agent 调度策略、抓取白/黑名单、导出与备份等。</p>
    </section>
  </div>
</template>

<style scoped>
.page {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  width: 100%;
  max-width: var(--maxw);
  margin: 0 auto;
  padding: 28px 20px 40px;
  display: flex;
  flex-direction: column;
  gap: 18px;
}

.page-head h1 {
  font-family: var(--font-display);
  font-size: 22px;
  font-weight: 700;
  margin: 0 0 4px;
}
.muted { color: var(--dim); font-size: 13.5px; line-height: 1.6; }

.card {
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: var(--radius);
  box-shadow: var(--shadow);
  padding: 18px 20px;
}
.card-ghost { border-style: dashed; box-shadow: none; }
.card-title {
  font-size: 15px;
  font-weight: 600;
  color: var(--ink);
  margin: 0 0 14px;
}

.form {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.divider {
  height: 1px;
  background: var(--line);
  margin: 4px 0;
}

.field {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.label {
  font-size: 12.5px;
  font-weight: 500;
  color: var(--dim);
  display: flex;
  align-items: center;
  gap: 8px;
}

.badge {
  font-size: 10.5px;
  font-weight: 500;
  font-family: var(--font-mono);
  padding: 2px 7px;
  border-radius: 999px;
  background: var(--accent-soft);
  color: var(--accent);
  letter-spacing: .04em;
}

.input-row {
  display: flex;
  gap: 6px;
  align-items: stretch;
}
.input-row input { flex: 1; }

input {
  appearance: none;
  border: 1px solid var(--line);
  background: var(--panel-2);
  color: var(--ink);
  font-family: var(--font-mono);
  font-size: 13.5px;
  padding: 9px 12px;
  border-radius: 9px;
  outline: none;
  transition: border-color .15s;
  min-width: 0;
}
input:focus { border-color: var(--accent); }
input::placeholder { color: var(--faint); font-family: var(--font-body); }

.ghost {
  appearance: none;
  border: 1px solid var(--line);
  background: var(--panel-2);
  color: var(--dim);
  width: 38px;
  border-radius: 9px;
  cursor: pointer;
  display: grid;
  place-items: center;
  transition: color .15s, border-color .15s;
}
.ghost:hover { color: var(--accent); border-color: var(--accent); }

.hint {
  font-size: 12px;
  color: var(--faint);
  margin-top: 2px;
}

/* ---- 模型列表 ---- */
.list-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.model-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13.5px;
}
.model-table th,
.model-table td {
  text-align: left;
  padding: 9px 10px;
  border-bottom: 1px solid var(--line);
}
.model-table th {
  font-size: 12px;
  font-weight: 500;
  color: var(--dim);
}
.model-table tbody tr:last-child td { border-bottom: none; }
.model-table tbody tr:hover td { background: var(--panel-2); }
.model-table .mono { font-family: var(--font-mono); }
.th-actions { width: 88px; }
.row-actions { display: flex; gap: 6px; }
.icon-btn {
  appearance: none;
  border: 1px solid var(--line);
  background: var(--panel-2);
  color: var(--dim);
  width: 30px;
  height: 30px;
  border-radius: 8px;
  cursor: pointer;
  display: grid;
  place-items: center;
  transition: color .15s, border-color .15s;
}
.icon-btn:hover { color: var(--accent); border-color: var(--accent); }
.icon-btn.danger:hover { color: var(--danger, #e5484d); border-color: var(--danger, #e5484d); }

select {
  appearance: none;
  border: 1px solid var(--line);
  background: var(--panel-2);
  color: var(--ink);
  font-family: var(--font-mono);
  font-size: 13.5px;
  padding: 9px 12px;
  border-radius: 9px;
  outline: none;
  transition: border-color .15s;
  min-width: 0;
}
select:focus { border-color: var(--accent); }
select:disabled { opacity: .5; cursor: not-allowed; }

.result {
  font-size: 12.5px;
  padding: 8px 12px;
  border-radius: 8px;
  word-break: break-word;
  font-family: var(--font-mono);
}
.result.ok {
  background: var(--accent-soft);
  color: var(--accent);
  border: 1px solid color-mix(in srgb, var(--accent) 35%, transparent);
}
.result.err {
  background: var(--danger-soft);
  color: var(--danger);
  border: 1px solid color-mix(in srgb, var(--danger) 35%, transparent);
}

.actions {
  display: flex;
  gap: 10px;
  justify-content: flex-end;
  margin-top: 6px;
}

.btn-ghost, .btn-primary {
  appearance: none;
  border: 1px solid var(--line);
  border-radius: 9px;
  padding: 0 18px;
  height: 40px;
  font-size: 13.5px;
  font-weight: 500;
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  gap: 8px;
  transition: border-color .15s, background .15s, transform .1s;
}
.btn-ghost { width: auto; background: var(--panel-2); color: var(--dim); }
.btn-ghost:hover { color: var(--accent); border-color: var(--accent); }
.btn-primary {
  background: var(--accent);
  color: var(--accent-ink);
  border-color: var(--accent);
}
.btn-primary:hover { filter: brightness(1.06); }
.btn-primary:active, .btn-ghost:active { transform: scale(.97); }
.btn-ghost:disabled, .btn-primary:disabled { opacity: .5; cursor: wait; }

.spin {
  width: 13px;
  height: 13px;
  border-radius: 50%;
  border: 2px solid currentColor;
  border-top-color: transparent;
  animation: rot .7s linear infinite;
}

/* 内置 anything-analyzer 状态展示 */
.builtin-hint {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 6px;
  padding: 8px 12px;
  font-size: 12px;
  background: color-mix(in srgb, var(--accent) 12%, transparent);
  border: 1px solid color-mix(in srgb, var(--accent) 35%, transparent);
  border-radius: var(--radius);
  color: var(--accent);
  flex-wrap: wrap;
}
.builtin-hint.warn {
  background: color-mix(in srgb, #f59e0b 10%, transparent);
  border-color: color-mix(in srgb, #f59e0b 40%, transparent);
  color: #f59e0b;
}
.builtin-dot {
  font-weight: 700;
  font-size: 13px;
}
.builtin-path {
  font-family: var(--font-mono);
  font-size: 11px;
  opacity: .75;
  word-break: break-all;
}
.ghost-link {
  font-size: 12px;
  color: var(--muted);
  border: none;
  padding: 2px 0;
}
.ghost-link:hover {
  color: var(--accent);
  border: none;
}

/* 背景图 */
.mcp-cmd {
  width: 100%;
  margin-top: 6px;
  padding: 8px 10px;
  font-family: var(--font-mono);
  font-size: 12px;
  background: color-mix(in srgb, var(--panel) 80%, transparent);
  border: 1px solid var(--line);
  border-radius: var(--radius);
  color: inherit;
  resize: vertical;
}
.bg-row {
  display: flex;
  gap: 10px;
  align-items: center;
  flex-wrap: wrap;
}
.file-input {
  flex: 1;
  min-width: 200px;
  font-family: var(--font-body);
}
.bg-preview {
  width: 100%;
  height: 140px;
  border-radius: 10px;
  border: 1px solid var(--line);
  background-size: cover;
  background-position: center;
  margin-top: 6px;
}
.bg-opacity-row {
  margin-top: 10px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.bg-opacity-row input[type="range"] {
  width: 100%;
  accent-color: var(--accent);
  cursor: pointer;
}
.bg-opacity-row .mono {
  font-family: var(--font-mono);
  color: var(--accent);
}

@media (max-width: 640px) {
  .page { padding: 20px 14px 24px; }
}
</style>
