// 设置面板状态：多服务商模型注册表（列表/添加/编辑/删除）、思考深度、连通性测试
// API Key 只在添加/编辑表单中提交，后端不出明文，前端只看 key_set
import { computed, reactive } from 'vue'

const SELECTED_MODEL_KEY = 'crawagent-selected-model'

const state = reactive({
  open: false,
  loading: false,
  saving: false,
  testing: false,
  models: [],              // [{name, provider}] 已添加的模型
  providers: [],           // [{name, base_url, key_set}] 服务商（Key 不出后端）
  model: '',               // 当前对话选中的模型 ID（前端本地持久化）
  thinkingDepth: 'off',    // off / low / high / max
  startBrowser: '',        // start 自动弹窗浏览器：'' 系统默认 / chrome / msedge / firefox
  testResult: null,        // {ok: bool, msg: string}
  saveTip: '',             // 操作提示文案
  saveTipOk: null,         // 与 saveTip 配对的成功/失败分级（true=成功绿 / false=失败红 / null=中性）
  // ---- 生态面板（P2-9）：技能 / MCP servers / 插件 ----
  ecoSkills: [],           // [{name, description, source: builtin|plugin}]
  ecoMcpServers: [],       // [{name, transport, url|command+args, headers(脱敏), disabled}]
  ecoMcpStatus: [],        // [{name, transport, endpoint, disabled, running, tools}]
  ecoPlugins: [],          // [{name, version, description, valid, error, dependencies}]
  ecoSkillsDirs: '',
  ecoLoading: false,
  showMcpForm: false,
  mcpForm: { name: '', transport: 'streamable_http', url: '', command: '', args: '' },
  // ---- 高级页（T2）：抓取参数 / 产物目录 / 保留清理策略 ----
  adv: {
    timeout: 30,           // 抓取超时（秒）
    delay: 1.0,            // 请求间隔（秒）
    outputDir: '',         // 文本产物目录（展示 + 打开，改目录走 .env）
    downloadsDir: '',      // 媒体下载目录（展示 + 打开）
    logsDays: null,        // 日志保留天数（null = 不清理）
    outputDays: null,      // 产物保留天数
    downloadsDays: null,   // 下载保留天数
    outputCapGb: null,     // 产物容量上限（GB，null = 不限）
    downloadsCapGb: null,  // 下载容量上限（GB）
  },
  // ---- LangSmith 追踪（可选调试）：后端 settings 已支持，保存写 .env ----
  langsmith: {
    apiKey: '',            // 回显掩码（****xxxx），含 * 视为掩码不回写
    project: 'crawagent',  // LangSmith 项目名
    tracing: false,        // 是否启用链路追踪（需重启 crawagent 生效）
  },
})

// 简化视图：给 App.vue/Header 等只读场景用
const config = computed(() => ({
  defaultModel: state.model || state.models[0]?.name || '',
  thinkingDepth: state.thinkingDepth,
}))

function _loadSelectedModel() {
  try { return localStorage.getItem(SELECTED_MODEL_KEY) || '' } catch (e) { return '' }
}

function _persistSelectedModel(v) {
  try { localStorage.setItem(SELECTED_MODEL_KEY, v || '') } catch (e) { /* ignore */ }
}

function _pickModel(models) {
  const names = models.map(m => m.name)
  const selected = _loadSelectedModel()
  if (selected && names.includes(selected)) return selected
  return names[0] || ''
}

function _applySnapshot(data) {
  // 容错：把字符串条目（旧格式）归一化为 {name, provider}，并过滤无效项
  const raw = Array.isArray(data.models) ? data.models : []
  state.models = raw
    .map(m => (typeof m === 'string' ? { name: m, provider: '' } : m))
    .filter(m => m && m.name)
  state.providers = Array.isArray(data.providers) ? data.providers : []
  state.model = _pickModel(state.models)
}

async function load() {
  state.loading = true
  try {
    const res = await fetch('/api/settings')
    if (!res.ok) return
    const data = await res.json()
    _applySnapshot(data)
    state.thinkingDepth = data.thinking_depth || 'off'
    state.startBrowser = data.start_browser || ''
    // 高级页字段（T2）
    state.adv = {
      timeout: data.request_timeout ?? 30,
      delay: data.request_delay ?? 1.0,
      outputDir: data.output_dir || '',
      downloadsDir: data.downloads_dir || '',
      logsDays: data.logs_retention_days ?? null,
      outputDays: data.output_retention_days ?? null,
      downloadsDays: data.downloads_retention_days ?? null,
      outputCapGb: data.output_max_size_gb ?? null,
      downloadsCapGb: data.downloads_max_size_gb ?? null,
    }
    // 有自定义命令时默认展开高级模式
    state.advancedMode = !!state.mcpStartCommand
    // LangSmith 追踪字段（后端脱敏回显 apiKey）
    state.langsmith = {
      apiKey: data.langsmith_api_key || '',
      project: data.langsmith_project || 'crawagent',
      tracing: !!data.langsmith_tracing,
    }
  } finally {
    state.loading = false
  }
}

function _post(url, body) {
  return fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }).then(r => r.json())
}

async function addModel(form) { // {provider, model, apiKey, baseUrl}
  state.saving = true
  state.saveTip = ''
  try {
    const data = await _post('/api/models', {
      provider: form.provider, model: form.model,
      api_key: form.apiKey, base_url: form.baseUrl,
    })
    if (_applyResult(data)) return true
  } catch (e) {
    _tip('添加失败：' + (e?.message || e), false)
  } finally {
    state.saving = false
  }
  return false
}

async function updateModel(form) { // {origProvider, origName, provider, model, apiKey, baseUrl}
  state.saving = true
  state.saveTip = ''
  try {
    const data = await _post('/api/models/update', {
      orig_provider: form.origProvider, orig_name: form.origName,
      provider: form.provider, model: form.model,
      api_key: form.apiKey, base_url: form.baseUrl,
    })
    if (_applyResult(data)) return true
  } catch (e) {
    _tip('保存失败：' + (e?.message || e), false)
  } finally {
    state.saving = false
  }
  return false
}

function _tip(msg, ok) {
  state.saveTip = msg
  state.saveTipOk = ok
}

function _applyResult(data) {
  if (data.ok) {
    _applySnapshot(data)
    return true
  }
  _tip(data.error || '未知错误', false)
  return false
}

async function deleteModel(provider, name) {
  try {
    const data = await _post('/api/models/delete', { provider, name })
    return _applyResult(data)
  } catch (e) {
    _tip('删除失败：' + (e?.message || e), false)
    return false
  }
}

async function saveThinking() {
  try {
    const data = await _post('/api/settings', { thinking_depth: state.thinkingDepth })
    if (data.ok) _tip('思考深度已保存', true)
    else _tip('保存失败：' + (data.error || '未知错误'), false)
  } catch (e) {
    _tip('保存失败：' + (e?.message || e), false)
  }
}

async function saveStartBrowser(val) {
  try {
    const data = await _post('/api/settings', { start_browser: val })
    if (data.ok) {
      state.startBrowser = data.start_browser ?? val
      return true
    }
    _tip('保存失败：' + (data.error || '未知错误'), false)
  } catch (e) {
    _tip('保存失败：' + (e?.message || e), false)
  }
  return false
}

async function test(form) { // {provider, model, apiKey, baseUrl}
  state.testing = true
  state.testResult = null
  state.saveTip = ''
  try {
    const data = await _post('/api/settings/test', {
      provider: form.provider, model: form.model,
      api_key: form.apiKey, base_url: form.baseUrl,
    })
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
  await loadAll()
}

function close() {
  state.open = false
}

function setSelectedModel(v) {
  if (!v) return
  state.model = v
  _persistSelectedModel(v)
}

// ---------- 生态面板（技能 / MCP servers / 插件）----------

async function loadEcosystem() {
  state.ecoLoading = true
  try {
    const r = await fetch('/api/ecosystem')
    if (!r.ok) return
    const d = await r.json()
    state.ecoSkills = d.skills || []
    state.ecoMcpServers = d.mcp_servers || []
    state.ecoMcpStatus = d.mcp_status || []
    state.ecoPlugins = d.plugins || []
    state.ecoSkillsDirs = d.skills_dirs || ''
  } catch (e) { /* 面板加载失败不打断设置页 */ } finally {
    state.ecoLoading = false
  }
}

async function saveMcpServers(servers) {
  state.saving = true
  state.saveTip = ''
  try {
    const data = await _post('/api/mcp/servers/save', { servers })
    if (data.ok) {
      state.ecoMcpServers = data.mcp_servers || []
      state.ecoMcpStatus = data.mcp_status || []
      _tip('✓ MCP 配置已保存并生效（下一轮对话使用新工具列表）', true)
      return true
    }
    _tip('保存失败：' + (data.error || '未知错误'), false)
  } catch (e) {
    _tip('保存失败：' + (e?.message || e), false)
  } finally {
    state.saving = false
  }
  return false
}

async function toggleServer(srv) {
  const next = state.ecoMcpServers.map(s =>
    s.name === srv.name ? { ...s, disabled: !s.disabled } : s
  )
  await saveMcpServers(next)
}

async function removeServer(srv) {
  if (!confirm(`移除 MCP 服务「${srv.name}」？（可随时重新添加）`)) return
  await saveMcpServers(state.ecoMcpServers.filter(s => s.name !== srv.name))
}

async function addMcpServer() {
  const f = state.mcpForm
  const entry = { name: f.name.trim(), transport: f.transport, disabled: false }
  if (f.transport === 'stdio') {
    entry.command = f.command.trim()
    entry.args = f.args.trim() ? f.args.trim().split(/\s+/) : []
  } else {
    entry.url = f.url.trim()
  }
  const ok = await saveMcpServers([...state.ecoMcpServers, entry])
  if (ok) {
    state.showMcpForm = false
    state.mcpForm = { name: '', transport: 'streamable_http', url: '', command: '', args: '' }
  }
}

async function openEcoFolder(folder) {
  try {
    const data = await _post('/api/ecosystem/open-folder', { folder })
    if (!data.ok) _tip(data.error || '打开失败', false)
  } catch (e) {
    _tip('请求失败：' + e.message, false)
  }
}

// 通用保存：payload 直传 /api/settings。返回 {ok, error}，提示与加载态由调用方就地管理
// （不写全局 saveTip/saving，避免跨卡片串显）
async function saveSettingsFields(payload) {
  try {
    const data = await _post('/api/settings', payload)
    return { ok: !!data.ok, error: data.error || '' }
  } catch (e) {
    return { ok: false, error: e?.message || String(e) }
  }
}

export function useSettings() {
  return {
    state, config,
    load, addModel, updateModel, deleteModel,
    saveThinking, test, open, close, setSelectedModel,
    loadEcosystem, saveMcpServers, toggleServer, removeServer, addMcpServer, openEcoFolder,
    saveStartBrowser, saveSettingsFields,
  }
}
