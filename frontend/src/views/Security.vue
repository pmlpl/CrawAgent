<template>
  <div class="security-page">
    <header class="page-header">
      <h2 class="font-display">安全扫描</h2>
    </header>

    <div class="help-banner" v-if="showHelp">
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
        <circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/>
      </svg>
      <span>
        <b>使用流程：</b>
        1) 填写目标 URL，选择扫描类别（可选），点击"开始扫描"
        2) 扫描可能耗时数十秒，完成后自动显示漏洞列表
        3) 点击任务列表中的"补丁"按钮可生成修复代码 diff
        4) 扫描深度和最大请求数影响扫描范围，数值越大越全面但耗时更长
        <br><span style="color: #dc3545;">⚠️ 请仅扫描已授权的目标，未经授权的扫描可能违法。</span>
      </span>
      <button class="help-close" @click="showHelp = false">×</button>
    </div>

    <div class="settings-content">
      <!-- 发起扫描 -->
      <div class="settings-section">
        <div class="section-title">新建扫描任务</div>
        <p class="section-desc">
          扫描目标 URL 的安全漏洞（OWASP Top 10），自动生成修复补丁。请仅扫描授权目标。
        </p>
        <div class="quick-form">
          <input v-model="form.name" class="quick-input" placeholder="任务名称（可选，如：官网安全基线）" />
          <input v-model="form.url" class="quick-input" placeholder="目标 URL（如 https://example.com）" />
          <div class="form-row">
            <input v-model.number="form.depth" class="quick-input" type="number" min="1" max="3" placeholder="扫描深度" />
            <input v-model.number="form.max_requests" class="quick-input" type="number" min="1" max="500" placeholder="最大请求数" />
          </div>
          <div class="cats-block">
            <div class="cats-label">扫描类别（不选则扫全部）</div>
            <div class="cats-grid">
              <label v-for="c in categories" :key="c.value" class="cat-chip" :class="{ 'cat-on': form.categories.includes(c.value) }">
                <input type="checkbox" :value="c.value" v-model="form.categories" />
                <span>{{ c.label }}</span>
              </label>
            </div>
          </div>
          <button class="primary-btn" :disabled="!form.url || scanning" @click="startScan">
            {{ scanning ? '扫描中（可能耗时数十秒）…' : '开始扫描' }}
          </button>
        </div>
        <div class="quick-result" v-if="scanResult">
          <div class="result-summary">
            <span>状态: <b>{{ scanResult.task?.status }}</b></span>
            <span>漏洞: <b :class="`sev-${scanResult.scan_result?.vulnerabilities?.length ? 'warn' : 'ok'}`">{{ scanResult.scan_result?.vulnerabilities?.length || 0 }}</b></span>
            <span>补丁: <b>{{ scanResult.patches?.length || 0 }}</b></span>
            <span>耗时: <b>{{ scanResult.scan_result?.duration_seconds?.toFixed(1) || '-' }}s</b></span>
          </div>
          <pre>{{ scanResultSummary }}</pre>
        </div>
      </div>

      <!-- 扫描任务列表 -->
      <div class="settings-section">
        <div class="section-title">
          扫描任务
          <button class="refresh-btn" @click="loadTasks" :disabled="tasksLoading">
            {{ tasksLoading ? '加载中…' : '刷新' }}
          </button>
        </div>
        <div class="files-list" v-if="tasks.length">
          <div v-for="t in tasks" :key="t.id" class="task-item" :class="{ 'task-active': selectedTaskId === t.id }" @click="selectTask(t.id)">
            <div class="task-info">
              <div class="task-name">
                {{ t.name || t.url }}
                <span class="task-badge" :class="`status-${t.status}`">{{ statusText(t.status) }}</span>
              </div>
              <div class="task-url">{{ t.url }}</div>
              <div class="task-meta">
                <span>漏洞: {{ t.vuln_count }}</span>
                <span v-if="t.categories?.length">类别: {{ t.categories.length }}</span>
                <span v-if="t.duration">耗时: {{ t.duration.toFixed(1) }}s</span>
              </div>
            </div>
            <div class="task-actions" @click.stop>
              <button class="action-btn" @click="rescan(t)" :disabled="rescanningId === t.id">
                {{ rescanningId === t.id ? '重扫中…' : '重扫' }}
              </button>
              <button class="action-btn" @click="genPatches(t)" :disabled="!t.vuln_count">补丁</button>
              <button class="del-btn" @click="deleteTask(t)" title="删除">×</button>
            </div>
          </div>
        </div>
        <div class="empty-state" v-else-if="!tasksLoading">
          暂无扫描任务
        </div>
      </div>

      <!-- 漏洞详情 -->
      <div class="settings-section" v-if="selectedTaskId">
        <div class="section-title">
          漏洞详情（{{ selectedTaskName }}）
          <button class="refresh-btn" @click="loadVulns" :disabled="vulnsLoading">
            {{ vulnsLoading ? '加载中…' : '刷新' }}
          </button>
        </div>
        <div class="filter-row">
          <select v-model="vulnFilter" class="quick-input filter-select" @change="loadVulns">
            <option value="">全部严重性</option>
            <option value="critical">严重</option>
            <option value="high">高危</option>
            <option value="medium">中危</option>
            <option value="low">低危</option>
            <option value="info">信息</option>
          </select>
        </div>
        <div class="files-list" v-if="vulns.length">
          <div v-for="v in vulns" :key="v.id" class="vuln-item" :class="`vuln-${v.severity}`">
            <div class="vuln-header">
              <span class="vuln-sev" :class="`sev-badge-${v.severity}`">{{ sevText(v.severity) }}</span>
              <span class="vuln-title">{{ v.title }}</span>
              <span class="vuln-cat">{{ v.category }}</span>
            </div>
            <div class="vuln-url">{{ v.url }}</div>
            <div class="vuln-meta">
              <span v-if="v.parameter">参数: <code>{{ v.parameter }}</code></span>
              <span v-if="v.method">方法: {{ v.method }}</span>
              <span>置信度: {{ Math.round((v.confidence || 0) * 100) }}%</span>
              <span v-if="v.cwe_id">CWE: {{ v.cwe_id }}</span>
            </div>
            <div class="vuln-evidence" v-if="v.evidence">
              <div class="evidence-label">证据:</div>
              <pre>{{ v.evidence }}</pre>
            </div>
            <div class="vuln-remediation" v-if="v.remediation">
              <div class="remediation-label">修复建议:</div>
              <div class="remediation-text">{{ v.remediation }}</div>
            </div>
          </div>
        </div>
        <div class="empty-state" v-else-if="!vulnsLoading">
          该任务无漏洞记录
        </div>
      </div>

      <!-- 补丁列表 -->
      <div class="settings-section" v-if="patches.length">
        <div class="section-title">修复补丁</div>
        <div class="files-list">
          <div v-for="p in patches" :key="p.id" class="patch-item">
            <div class="patch-header">
              <span class="vuln-sev" :class="`sev-badge-${p.severity}`">{{ sevText(p.severity) }}</span>
              <span class="patch-title">{{ p.title }}</span>
              <span class="patch-target">→ {{ p.target_file }}</span>
            </div>
            <div class="patch-desc" v-if="p.description">{{ p.description }}</div>
            <pre class="patch-diff">{{ p.diff }}</pre>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import axios from 'axios'

const api = axios.create({ baseURL: '/api', timeout: 180000 })

const showHelp = ref(true)

const form = ref({
  name: '',
  url: '',
  categories: [],
  depth: 1,
  max_requests: 100,
})
const categories = ref([])
const scanning = ref(false)
const scanResult = ref('')
const scanResultSummary = computed(() => {
  if (!scanResult.value) return ''
  const r = scanResult.value
  const vulns = r.scan_result?.vulnerabilities || []
  if (!vulns.length) return '未发现漏洞'
  return vulns.map(v => `[${v.severity}] ${v.title} @ ${v.url}`).join('\n')
})

const tasks = ref([])
const tasksLoading = ref(false)
const rescanningId = ref('')

const selectedTaskId = ref('')
const selectedTaskName = ref('')
const vulns = ref([])
const vulnsLoading = ref(false)
const vulnFilter = ref('')

const patches = ref([])

onMounted(() => {
  loadCategories()
  loadTasks()
})

async function loadCategories() {
  try {
    const res = await api.get('/security/categories')
    categories.value = res.data.categories || []
  } catch (e) {
    console.error('加载类别失败:', e)
  }
}

async function loadTasks() {
  tasksLoading.value = true
  try {
    const res = await api.get('/security/tasks')
    tasks.value = res.data.tasks || []
  } catch (e) {
    console.error('加载任务失败:', e)
  } finally {
    tasksLoading.value = false
  }
}

async function startScan() {
  if (!form.value.url) return
  scanning.value = true
  scanResult.value = ''
  patches.value = []
  try {
    const res = await api.post('/security/tasks', {
      name: form.value.name,
      url: form.value.url,
      categories: form.value.categories,
      depth: form.value.depth,
      max_requests: form.value.max_requests,
      generate_patches: true,
    })
    scanResult.value = res.data
    patches.value = res.data.patches || []
    await loadTasks()
    if (res.data.task?.id) {
      selectTask(res.data.task.id, res.data.task.name)
    }
  } catch (e) {
    scanResult.value = { error: e.response?.data?.detail || e.message }
  } finally {
    scanning.value = false
  }
}

async function rescan(task) {
  rescanningId.value = task.id
  patches.value = []
  try {
    const res = await api.post(`/security/tasks/${task.id}/rescan`)
    patches.value = res.data.patches || []
    await loadTasks()
    if (selectedTaskId.value === task.id) {
      await loadVulns()
    }
    alert(`重扫完成：发现 ${res.data.scan_result?.vulnerabilities?.length || 0} 个漏洞`)
  } catch (e) {
    alert(`重扫失败: ${e.response?.data?.detail || e.message}`)
  } finally {
    rescanningId.value = ''
  }
}

async function genPatches(task) {
  try {
    const res = await api.post(`/security/tasks/${task.id}/patches`)
    patches.value = res.data.patches || []
    alert(`已生成 ${res.data.patches_generated} 个补丁，保存到 ${res.data.patch_files?.length || 0} 个文件`)
  } catch (e) {
    alert(`生成补丁失败: ${e.response?.data?.detail || e.message}`)
  }
}

async function deleteTask(task) {
  if (!confirm(`确认删除扫描任务「${task.name || task.url}」？\n关联的漏洞记录也会被删除。`)) return
  try {
    await api.delete(`/security/tasks/${task.id}`)
    tasks.value = tasks.value.filter(t => t.id !== task.id)
    if (selectedTaskId.value === task.id) {
      selectedTaskId.value = ''
      vulns.value = []
    }
  } catch (e) {
    alert(`删除失败: ${e.response?.data?.detail || e.message}`)
  }
}

function selectTask(taskId, taskName) {
  selectedTaskId.value = taskId
  selectedTaskName.value = taskName || tasks.value.find(t => t.id === taskId)?.name || taskId
  loadVulns()
}

async function loadVulns() {
  if (!selectedTaskId.value) return
  vulnsLoading.value = true
  try {
    const params = {}
    if (vulnFilter.value) params.severity = vulnFilter.value
    const res = await api.get(`/security/tasks/${selectedTaskId.value}/vulnerabilities`, { params })
    vulns.value = res.data.vulnerabilities || []
  } catch (e) {
    console.error('加载漏洞失败:', e)
  } finally {
    vulnsLoading.value = false
  }
}

function statusText(status) {
  return { pending: '待扫', running: '运行中', completed: '已完成', failed: '失败' }[status] || status
}

function sevText(sev) {
  return { info: '信息', low: '低危', medium: '中危', high: '高危', critical: '严重' }[sev] || sev
}
</script>

<style scoped>
.security-page { padding: 24px 32px; max-width: 900px; margin: 0 auto; }
.page-header { margin-bottom: 16px; }
.page-header h2 { font-size: 24px; margin: 0; }
.help-banner { display: flex; align-items: flex-start; gap: 10px; padding: 12px 16px; background: var(--surface); border: 1px solid var(--border); border-radius: 8px; margin-bottom: 20px; font-size: 13px; line-height: 1.6; }
.help-banner svg { flex-shrink: 0; margin-top: 2px; color: var(--accent); }
.help-banner b { color: var(--text); }
.help-banner code { background: var(--bg); padding: 1px 6px; border-radius: 4px; font-family: monospace; font-size: 12px; }
.help-close { margin-left: auto; background: none; border: none; color: var(--text-muted); cursor: pointer; font-size: 18px; line-height: 1; padding: 0 4px; }
.help-close:hover { color: var(--text); }
.settings-content { display: flex; flex-direction: column; gap: 24px; }
.settings-section { background: var(--surface); border-radius: 12px; padding: 20px 24px; border: 1px solid var(--border); }
.section-title { font-size: 15px; font-weight: 600; margin-bottom: 12px; display: flex; align-items: center; justify-content: space-between; }
.section-desc { font-size: 13px; color: var(--text-muted); margin: 0 0 12px; line-height: 1.6; }
.quick-form { display: flex; flex-direction: column; gap: 8px; }
.form-row { display: flex; gap: 8px; }
.form-row .quick-input { flex: 1; }
.quick-input { width: 100%; padding: 10px 12px; border: 1px solid var(--border); border-radius: 8px; background: var(--bg); color: var(--text); font-size: 14px; outline: none; }
.quick-input:focus { border-color: var(--accent); }
.cats-block { margin: 4px 0; }
.cats-label { font-size: 13px; color: var(--text-muted); margin-bottom: 6px; }
.cats-grid { display: flex; flex-wrap: wrap; gap: 6px; }
.cat-chip { display: inline-flex; align-items: center; gap: 4px; padding: 4px 10px; border: 1px solid var(--border); border-radius: 16px; font-size: 12px; cursor: pointer; color: var(--text-muted); }
.cat-chip input { display: none; }
.cat-on { background: var(--accent); color: white; border-color: var(--accent); }
.primary-btn { align-self: flex-start; padding: 8px 20px; background: var(--accent); color: white; border: none; border-radius: 8px; cursor: pointer; font-size: 14px; }
.primary-btn:hover:not(:disabled) { opacity: 0.9; }
.primary-btn:disabled { opacity: 0.5; cursor: not-allowed; }
.refresh-btn { padding: 4px 12px; background: transparent; color: var(--accent); border: 1px solid var(--border); border-radius: 6px; cursor: pointer; font-size: 12px; }
.refresh-btn:hover:not(:disabled) { background: var(--bg); }
.quick-result { margin-top: 12px; background: var(--bg); border-radius: 8px; padding: 12px; border: 1px solid var(--border); }
.result-summary { display: flex; gap: 16px; margin-bottom: 8px; font-size: 13px; }
.result-summary b { font-weight: 600; }
.sev-ok { color: #28a745; }
.sev-warn { color: #dc3545; }
.quick-result pre { margin: 0; font-size: 12px; white-space: pre-wrap; word-break: break-all; max-height: 300px; overflow-y: auto; color: var(--text); }
.files-list { display: flex; flex-direction: column; gap: 6px; }
.task-item { display: flex; justify-content: space-between; align-items: center; padding: 12px; background: var(--bg); border-radius: 8px; border: 1px solid transparent; cursor: pointer; }
.task-item:hover { border-color: var(--accent); }
.task-active { border-color: var(--accent); background: rgba(0, 123, 255, 0.05); }
.task-info { flex: 1; min-width: 0; }
.task-name { font-size: 14px; font-weight: 500; margin-bottom: 4px; display: flex; align-items: center; gap: 8px; }
.task-badge { font-size: 11px; padding: 1px 6px; border-radius: 4px; }
.status-pending { background: rgba(108, 117, 125, 0.15); color: #6c757d; }
.status-running { background: rgba(0, 123, 255, 0.15); color: #007bff; }
.status-completed { background: rgba(40, 167, 69, 0.15); color: #28a745; }
.status-failed { background: rgba(220, 53, 69, 0.15); color: #dc3545; }
.task-url { font-size: 12px; color: var(--text-muted); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.task-meta { font-size: 12px; color: var(--text-muted); margin-top: 4px; display: flex; gap: 12px; }
.task-actions { display: flex; align-items: center; gap: 6px; flex-shrink: 0; }
.action-btn { padding: 4px 10px; background: transparent; color: var(--accent); border: 1px solid var(--border); border-radius: 6px; cursor: pointer; font-size: 12px; }
.action-btn:hover:not(:disabled) { background: var(--bg); }
.del-btn { width: 22px; height: 22px; border-radius: 50%; background: transparent; color: var(--text-muted); border: none; cursor: pointer; font-size: 16px; line-height: 1; }
.del-btn:hover { background: rgba(220, 53, 69, 0.12); color: #dc3545; }
.filter-row { margin-bottom: 10px; }
.filter-select { max-width: 200px; }
.vuln-item { padding: 12px; background: var(--bg); border-radius: 8px; border-left: 3px solid var(--text-muted); }
.vuln-critical { border-left-color: #6f42c1; }
.vuln-high { border-left-color: #dc3545; }
.vuln-medium { border-left-color: #ffc107; }
.vuln-low { border-left-color: #17a2b8; }
.vuln-info { border-left-color: #6c757d; }
.vuln-header { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; flex-wrap: wrap; }
.vuln-sev { font-size: 11px; font-weight: 600; padding: 2px 8px; border-radius: 4px; }
.sev-badge-critical { background: rgba(111, 66, 193, 0.15); color: #6f42c1; }
.sev-badge-high { background: rgba(220, 53, 69, 0.15); color: #dc3545; }
.sev-badge-medium { background: rgba(255, 193, 7, 0.15); color: #b8860b; }
.sev-badge-low { background: rgba(23, 162, 184, 0.15); color: #17a2b8; }
.sev-badge-info { background: rgba(108, 117, 125, 0.15); color: #6c757d; }
.vuln-title { font-size: 14px; font-weight: 500; flex: 1; }
.vuln-cat { font-size: 11px; color: var(--text-muted); font-family: monospace; }
.vuln-url { font-size: 12px; color: var(--text-muted); margin-bottom: 4px; word-break: break-all; }
.vuln-meta { font-size: 12px; color: var(--text-muted); display: flex; gap: 12px; flex-wrap: wrap; }
.vuln-meta code { background: rgba(0,0,0,0.05); padding: 1px 4px; border-radius: 3px; font-size: 11px; }
.vuln-evidence { margin-top: 8px; }
.evidence-label { font-size: 12px; color: var(--text-muted); margin-bottom: 2px; }
.vuln-evidence pre { margin: 0; font-size: 11px; background: rgba(220, 53, 69, 0.05); padding: 8px; border-radius: 4px; white-space: pre-wrap; word-break: break-all; max-height: 150px; overflow-y: auto; }
.vuln-remediation { margin-top: 8px; }
.remediation-label { font-size: 12px; color: #28a745; margin-bottom: 2px; font-weight: 500; }
.remediation-text { font-size: 12px; color: var(--text); line-height: 1.5; }
.patch-item { padding: 12px; background: var(--bg); border-radius: 8px; border-left: 3px solid #28a745; }
.patch-header { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; flex-wrap: wrap; }
.patch-title { font-size: 14px; font-weight: 500; flex: 1; }
.patch-target { font-size: 12px; color: var(--accent); font-family: monospace; }
.patch-desc { font-size: 12px; color: var(--text-muted); margin-bottom: 8px; line-height: 1.5; }
.patch-diff { margin: 0; font-size: 11px; background: rgba(0,0,0,0.05); padding: 8px; border-radius: 4px; white-space: pre-wrap; word-break: break-all; max-height: 200px; overflow-y: auto; font-family: monospace; }
.empty-state { text-align: center; padding: 40px; color: var(--text-muted); font-size: 14px; }
</style>
