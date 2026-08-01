<template>
  <div class="monitor-page">
    <header class="page-header">
      <h2 class="font-display">监控</h2>
    </header>

    <div class="settings-content">
      <!-- 创建监控任务 -->
      <div class="settings-section">
        <div class="section-title">新建监控任务</div>
        <p class="section-desc">
          监控目标 URL 的变化，支持字段级监控（price/stock）和整体内容变化。变化时通过 Webhook 通知。
        </p>
        <div class="quick-form">
          <input v-model="form.name" class="quick-input" placeholder="任务名称（可选，如：iPhone 价格监控）" />
          <input v-model="form.url" class="quick-input" placeholder="监控 URL（如 https://www.apple.com/shop/buy-iphone）" />
          <div class="form-row">
            <input v-model.number="form.interval_minutes" class="quick-input" type="number" min="1" placeholder="间隔（分钟）" />
            <input v-model="form.watch_fields_str" class="quick-input" placeholder="监控字段（逗号分隔，如 price,stock）" />
          </div>
          <input v-model="form.css_selector" class="quick-input" placeholder="CSS 选择器（可选，如 .price-tag）" />
          <input v-model="form.alert_webhook" class="quick-input" placeholder="Webhook URL（变化时通知，留空则不通知）" />
          <div class="form-row">
            <input v-model.number="form.alert_cooldown_minutes" class="quick-input" type="number" min="1" placeholder="告警冷却（分钟）" />
            <label class="checkbox-label">
              <input type="checkbox" v-model="form.enabled" /> 启用
            </label>
          </div>
          <button class="primary-btn" :disabled="!form.url || creating" @click="createTask">
            {{ creating ? '创建中（首次检查）…' : '创建任务' }}
          </button>
        </div>
        <div class="quick-result" v-if="createResult">
          <pre>{{ createResult }}</pre>
        </div>
      </div>

      <!-- 监控任务列表 -->
      <div class="settings-section">
        <div class="section-title">
          监控任务
          <button class="refresh-btn" @click="loadTasks" :disabled="tasksLoading">
            {{ tasksLoading ? '加载中…' : '刷新' }}
          </button>
        </div>
        <div class="files-list" v-if="tasks.length">
          <div v-for="t in tasks" :key="t.id" class="task-item">
            <div class="task-info">
              <div class="task-name">
                {{ t.name }}
                <span class="task-badge" :class="t.enabled ? 'badge-on' : 'badge-off'">
                  {{ t.enabled ? '启用' : '停用' }}
                </span>
              </div>
              <div class="task-url">{{ t.url }}</div>
              <div class="task-meta">
                <span>间隔: {{ Math.round(t.interval_seconds / 60) }} 分钟</span>
                <span v-if="t.watch_fields?.length">字段: {{ t.watch_fields.join(', ') }}</span>
                <span v-if="t.consecutive_failures > 0" class="fail-count">连续失败: {{ t.consecutive_failures }}</span>
              </div>
            </div>
            <div class="task-actions">
              <button class="action-btn" @click="checkNow(t.id)" :disabled="checkingId === t.id">
                {{ checkingId === t.id ? '检查中…' : '检查' }}
              </button>
              <button class="action-btn toggle-btn" @click="toggleTask(t)">
                {{ t.enabled ? '停用' : '启用' }}
              </button>
              <button class="del-btn" @click="deleteTask(t)" title="删除">×</button>
            </div>
          </div>
        </div>
        <div class="empty-state" v-else-if="!tasksLoading">
          暂无监控任务
        </div>
      </div>

      <!-- 告警记录 -->
      <div class="settings-section">
        <div class="section-title">
          告警记录
          <button class="refresh-btn" @click="loadAlerts" :disabled="alertsLoading">
            {{ alertsLoading ? '加载中…' : '刷新' }}
          </button>
        </div>
        <div class="files-list" v-if="alerts.length">
          <div v-for="a in alerts" :key="a.id" class="alert-item" :class="`alert-${a.level}`">
            <div class="alert-header">
              <span class="alert-level">{{ levelText(a.level) }}</span>
              <span class="alert-title">{{ a.title }}</span>
              <span class="alert-time">{{ formatDate(a.created_at) }}</span>
            </div>
            <div class="alert-msg">{{ a.message }}</div>
            <div class="alert-diff" v-if="a.diff_summary">{{ a.diff_summary }}</div>
            <div class="alert-values" v-if="a.old_value || a.new_value">
              <span class="old-val">{{ a.old_value || '∅' }}</span>
              <span class="arrow">→</span>
              <span class="new-val">{{ a.new_value || '∅' }}</span>
            </div>
          </div>
        </div>
        <div class="empty-state" v-else-if="!alertsLoading">
          暂无告警记录
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import axios from 'axios'

const api = axios.create({ baseURL: '/api', timeout: 120000 })

const form = ref({
  name: '',
  url: '',
  interval_minutes: 360,
  watch_fields_str: '',
  css_selector: '',
  alert_webhook: '',
  alert_cooldown_minutes: 60,
  enabled: true,
})
const creating = ref(false)
const createResult = ref('')

const tasks = ref([])
const tasksLoading = ref(false)
const checkingId = ref('')

const alerts = ref([])
const alertsLoading = ref(false)

onMounted(() => {
  loadTasks()
  loadAlerts()
})

async function loadTasks() {
  tasksLoading.value = true
  try {
    const res = await api.get('/monitor/tasks')
    tasks.value = res.data.tasks || []
  } catch (e) {
    console.error('加载任务失败:', e)
  } finally {
    tasksLoading.value = false
  }
}

async function loadAlerts() {
  alertsLoading.value = true
  try {
    const res = await api.get('/monitor/alerts', { params: { limit: 50 } })
    alerts.value = res.data.alerts || []
  } catch (e) {
    console.error('加载告警失败:', e)
  } finally {
    alertsLoading.value = false
  }
}

async function createTask() {
  if (!form.value.url) return
  creating.value = true
  createResult.value = ''
  try {
    const watch_fields = form.value.watch_fields_str
      ? form.value.watch_fields_str.split(',').map(s => s.trim()).filter(Boolean)
      : []
    const res = await api.post('/monitor/tasks', {
      name: form.value.name,
      url: form.value.url,
      interval_minutes: form.value.interval_minutes,
      watch_fields,
      css_selector: form.value.css_selector,
      alert_webhook: form.value.alert_webhook,
      alert_cooldown_minutes: form.value.alert_cooldown_minutes,
      enabled: form.value.enabled,
    })
    createResult.value = JSON.stringify(res.data.first_check || res.data.task, null, 2)
    // 重新加载列表
    await loadTasks()
    await loadAlerts()
    // 清空表单
    form.value.name = ''
    form.value.url = ''
    form.value.watch_fields_str = ''
    form.value.css_selector = ''
    form.value.alert_webhook = ''
  } catch (e) {
    createResult.value = `创建失败: ${e.response?.data?.detail || e.message}`
  } finally {
    creating.value = false
  }
}

async function checkNow(taskId) {
  checkingId.value = taskId
  try {
    const res = await api.post(`/monitor/tasks/${taskId}/check`)
    alert(`检查结果：${res.data.summary || JSON.stringify(res.data)}`)
    await loadTasks()
    await loadAlerts()
  } catch (e) {
    alert(`检查失败: ${e.response?.data?.detail || e.message}`)
  } finally {
    checkingId.value = ''
  }
}

async function toggleTask(task) {
  try {
    await api.put(`/monitor/tasks/${task.id}`, {
      name: task.name,
      url: task.url,
      interval_minutes: Math.round(task.interval_seconds / 60),
      watch_fields: task.watch_fields,
      css_selector: task.css_selector,
      alert_webhook: task.alert_webhook,
      alert_cooldown_minutes: Math.round(task.alert_cooldown / 60),
      enabled: !task.enabled,
    })
    await loadTasks()
  } catch (e) {
    alert(`更新失败: ${e.response?.data?.detail || e.message}`)
  }
}

async function deleteTask(task) {
  if (!confirm(`确认删除任务「${task.name}」？\n关联的基线和告警记录也会被删除。`)) return
  try {
    await api.delete(`/monitor/tasks/${task.id}`)
    tasks.value = tasks.value.filter(t => t.id !== task.id)
    await loadAlerts()
  } catch (e) {
    alert(`删除失败: ${e.response?.data?.detail || e.message}`)
  }
}

function levelText(level) {
  return { info: 'ℹ️ 信息', warn: '⚠️ 警告', error: '🔴 错误', critical: '🚨 严重' }[level] || level
}

function formatDate(ts) {
  if (!ts) return ''
  const d = new Date(ts * 1000)
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}
</script>

<style scoped>
.monitor-page { padding: 24px 32px; max-width: 900px; margin: 0 auto; }
.page-header { margin-bottom: 24px; }
.page-header h2 { font-size: 24px; margin: 0; }
.settings-content { display: flex; flex-direction: column; gap: 24px; }
.settings-section { background: var(--surface); border-radius: 12px; padding: 20px 24px; border: 1px solid var(--border); }
.section-title { font-size: 15px; font-weight: 600; margin-bottom: 12px; display: flex; align-items: center; justify-content: space-between; }
.section-desc { font-size: 13px; color: var(--text-muted); margin: 0 0 12px; line-height: 1.6; }
.quick-form { display: flex; flex-direction: column; gap: 8px; }
.form-row { display: flex; gap: 8px; }
.form-row .quick-input { flex: 1; }
.quick-input { width: 100%; padding: 10px 12px; border: 1px solid var(--border); border-radius: 8px; background: var(--bg); color: var(--text); font-size: 14px; outline: none; }
.quick-input:focus { border-color: var(--accent); }
.checkbox-label { display: flex; align-items: center; gap: 6px; font-size: 14px; color: var(--text-muted); white-space: nowrap; }
.primary-btn { align-self: flex-start; padding: 8px 20px; background: var(--accent); color: white; border: none; border-radius: 8px; cursor: pointer; font-size: 14px; }
.primary-btn:hover:not(:disabled) { opacity: 0.9; }
.primary-btn:disabled { opacity: 0.5; cursor: not-allowed; }
.refresh-btn { padding: 4px 12px; background: transparent; color: var(--accent); border: 1px solid var(--border); border-radius: 6px; cursor: pointer; font-size: 12px; }
.refresh-btn:hover:not(:disabled) { background: var(--bg); }
.quick-result { margin-top: 12px; background: var(--bg); border-radius: 8px; padding: 12px; border: 1px solid var(--border); }
.quick-result pre { margin: 0; font-size: 12px; white-space: pre-wrap; word-break: break-all; max-height: 300px; overflow-y: auto; color: var(--text); }
.files-list { display: flex; flex-direction: column; gap: 6px; }
.task-item { display: flex; justify-content: space-between; align-items: center; padding: 12px; background: var(--bg); border-radius: 8px; border: 1px solid transparent; }
.task-info { flex: 1; min-width: 0; }
.task-name { font-size: 14px; font-weight: 500; margin-bottom: 4px; display: flex; align-items: center; gap: 8px; }
.task-badge { font-size: 11px; padding: 1px 6px; border-radius: 4px; }
.badge-on { background: rgba(40, 167, 69, 0.15); color: #28a745; }
.badge-off { background: rgba(108, 117, 125, 0.15); color: #6c757d; }
.task-url { font-size: 12px; color: var(--text-muted); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.task-meta { font-size: 12px; color: var(--text-muted); margin-top: 4px; display: flex; gap: 12px; }
.fail-count { color: #dc3545; }
.task-actions { display: flex; align-items: center; gap: 6px; flex-shrink: 0; }
.action-btn { padding: 4px 10px; background: transparent; color: var(--accent); border: 1px solid var(--border); border-radius: 6px; cursor: pointer; font-size: 12px; }
.action-btn:hover:not(:disabled) { background: var(--bg); }
.toggle-btn { color: var(--text-muted); }
.del-btn { width: 22px; height: 22px; border-radius: 50%; background: transparent; color: var(--text-muted); border: none; cursor: pointer; font-size: 16px; line-height: 1; }
.del-btn:hover { background: rgba(220, 53, 69, 0.12); color: #dc3545; }
.alert-item { padding: 10px 12px; background: var(--bg); border-radius: 8px; border-left: 3px solid var(--text-muted); }
.alert-info { border-left-color: #17a2b8; }
.alert-warn { border-left-color: #ffc107; }
.alert-error { border-left-color: #dc3545; }
.alert-critical { border-left-color: #6f42c1; }
.alert-header { display: flex; align-items: center; gap: 8px; margin-bottom: 4px; }
.alert-level { font-size: 12px; font-weight: 600; }
.alert-title { font-size: 13px; flex: 1; }
.alert-time { font-size: 11px; color: var(--text-muted); }
.alert-msg { font-size: 12px; color: var(--text-muted); }
.alert-diff { font-size: 12px; color: var(--accent); margin-top: 4px; }
.alert-values { display: flex; align-items: center; gap: 8px; margin-top: 6px; font-size: 12px; }
.old-val { color: var(--text-muted); text-decoration: line-through; }
.arrow { color: var(--text-muted); }
.new-val { color: var(--accent); font-weight: 500; }
.empty-state { text-align: center; padding: 40px; color: var(--text-muted); font-size: 14px; }
</style>
