<template>
  <div class="output-page">
    <header class="page-header">
      <h2 class="font-display">文件整理</h2>
    </header>

    <div class="settings-content">
      <!-- 路径模板预览 -->
      <div class="settings-section">
        <div class="section-title">路径模板预览</div>
        <p class="section-desc">
          支持占位符：<code>{domain}</code> <code>{date}</code> <code>{year}</code>
          <code>{month}</code> <code>{day}</code> <code>{title}</code> <code>{slug}</code>
          <code>{ext}</code>。非法字符（\ / : * ? " &lt; &gt; |）自动转义为 _。
        </p>
        <div class="quick-form">
          <input
            v-model="tplTemplate"
            class="quick-input"
            placeholder="articles/{domain}/{date}/{title}.{ext}"
          />
          <input
            v-model="tplUrl"
            class="quick-input"
            placeholder="源 URL（用于提取 domain）"
          />
          <input
            v-model="tplTitle"
            class="quick-input"
            placeholder="标题（含非法字符测试）"
          />
          <button class="primary-btn" :disabled="!tplTemplate || previewLoading" @click="previewPath">
            {{ previewLoading ? '渲染中…' : '预览路径' }}
          </button>
        </div>
        <div class="quick-result" v-if="previewResult">
          <div class="preview-path">
            <span class="preview-label">渲染结果：</span>
            <code class="preview-code">{{ previewResult }}</code>
          </div>
        </div>
      </div>

      <!-- 已保存文件列表 -->
      <div class="settings-section">
        <div class="section-title">
          已保存文件
          <button class="refresh-btn" @click="loadFiles" :disabled="filesLoading">
            {{ filesLoading ? '加载中…' : '刷新' }}
          </button>
        </div>
        <div class="files-list" v-if="files.length">
          <div
            v-for="f in files"
            :key="f.path"
            class="file-item"
            @click="viewFile(f)"
          >
            <div class="file-info">
              <div class="file-name">{{ f.name }}</div>
              <div class="file-path">{{ f.relative_path }}</div>
            </div>
            <div class="file-meta">
              <span class="file-size">{{ formatSize(f.size) }}</span>
              <span class="file-date">{{ formatDate(f.modified) }}</span>
              <button
                class="del-btn"
                @click.stop="deleteFile(f)"
                title="删除"
              >×</button>
            </div>
          </div>
        </div>
        <div class="empty-state" v-else-if="!filesLoading">
          暂无已保存文件
        </div>
      </div>

      <!-- 文件内容查看 -->
      <div class="settings-section" v-if="currentFile">
        <div class="section-title">
          {{ currentFile.name }}
          <button class="refresh-btn" @click="currentFile = null">关闭</button>
        </div>
        <div class="quick-result">
          <pre>{{ fileContent }}</pre>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import axios from 'axios'

const api = axios.create({ baseURL: '/api', timeout: 30000 })

const tplTemplate = ref('articles/{domain}/{date}/{title}.{ext}')
const tplUrl = ref('https://www.ruanyifeng.com/blog/2024/01/test.html')
const tplTitle = ref('测试标题：含非法字符<>|')
const previewResult = ref('')
const previewLoading = ref(false)

const files = ref([])
const filesLoading = ref(false)
const currentFile = ref(null)
const fileContent = ref('')

onMounted(() => {
  loadFiles()
})

async function previewPath() {
  if (!tplTemplate.value) return
  previewLoading.value = true
  try {
    const res = await api.post('/output/preview-path', {
      template: tplTemplate.value,
      url: tplUrl.value,
      title: tplTitle.value,
      ext: 'md',
    })
    previewResult.value = res.data.rendered_path
  } catch (e) {
    previewResult.value = `渲染失败: ${e.response?.data?.detail || e.message}`
  } finally {
    previewLoading.value = false
  }
}

async function loadFiles() {
  filesLoading.value = true
  try {
    const res = await api.get('/output/files', { params: { limit: 200 } })
    files.value = res.data.files || []
  } catch (e) {
    console.error('加载文件列表失败:', e)
    files.value = []
  } finally {
    filesLoading.value = false
  }
}

async function viewFile(f) {
  currentFile.value = f
  fileContent.value = '加载中…'
  try {
    const res = await api.get('/output/file', { params: { path: f.path } })
    fileContent.value = res.data.content
  } catch (e) {
    fileContent.value = `读取失败: ${e.response?.data?.detail || e.message}`
  }
}

async function deleteFile(f) {
  if (!confirm(`确认删除 ${f.name}？`)) return
  try {
    await api.delete('/output/file', { params: { path: f.path } })
    files.value = files.value.filter(x => x.path !== f.path)
    if (currentFile.value?.path === f.path) currentFile.value = null
  } catch (e) {
    alert(`删除失败: ${e.response?.data?.detail || e.message}`)
  }
}

function formatSize(bytes) {
  if (!bytes) return '0 B'
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

function formatDate(ts) {
  if (!ts) return ''
  const d = new Date(ts * 1000)
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}
</script>

<style scoped>
.output-page {
  padding: 24px 32px;
  max-width: 900px;
  margin: 0 auto;
}

.page-header {
  margin-bottom: 24px;
}

.page-header h2 {
  font-size: 24px;
  margin: 0;
}

.settings-content {
  display: flex;
  flex-direction: column;
  gap: 24px;
}

.settings-section {
  background: var(--surface);
  border-radius: 12px;
  padding: 20px 24px;
  border: 1px solid var(--border);
}

.section-title {
  font-size: 15px;
  font-weight: 600;
  margin-bottom: 12px;
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.section-desc {
  font-size: 13px;
  color: var(--text-muted);
  margin: 0 0 12px;
  line-height: 1.6;
}

.section-desc code {
  background: var(--bg);
  padding: 1px 6px;
  border-radius: 4px;
  font-size: 12px;
  color: var(--accent);
  margin: 0 2px;
}

.quick-form {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.quick-input {
  width: 100%;
  padding: 10px 12px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--bg);
  color: var(--text);
  font-size: 14px;
  outline: none;
  transition: border-color 0.2s;
}

.quick-input:focus {
  border-color: var(--accent);
}

.primary-btn {
  align-self: flex-start;
  padding: 8px 20px;
  background: var(--accent);
  color: white;
  border: none;
  border-radius: 8px;
  cursor: pointer;
  font-size: 14px;
  transition: opacity 0.2s;
}

.primary-btn:hover:not(:disabled) {
  opacity: 0.9;
}

.primary-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.refresh-btn {
  padding: 4px 12px;
  background: transparent;
  color: var(--accent);
  border: 1px solid var(--border);
  border-radius: 6px;
  cursor: pointer;
  font-size: 12px;
}

.refresh-btn:hover:not(:disabled) {
  background: var(--bg);
}

.quick-result {
  margin-top: 12px;
  background: var(--bg);
  border-radius: 8px;
  padding: 12px;
  border: 1px solid var(--border);
}

.quick-result pre {
  margin: 0;
  font-size: 12px;
  white-space: pre-wrap;
  word-break: break-all;
  max-height: 400px;
  overflow-y: auto;
  color: var(--text);
}

.preview-path {
  display: flex;
  align-items: center;
  gap: 8px;
}

.preview-label {
  font-size: 13px;
  color: var(--text-muted);
}

.preview-code {
  font-size: 13px;
  color: var(--accent);
  word-break: break-all;
}

.files-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.file-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 10px 12px;
  background: var(--bg);
  border-radius: 8px;
  cursor: pointer;
  border: 1px solid transparent;
  transition: border-color 0.2s;
}

.file-item:hover {
  border-color: var(--accent);
}

.file-info {
  flex: 1;
  min-width: 0;
}

.file-name {
  font-size: 14px;
  font-weight: 500;
  color: var(--text);
  margin-bottom: 2px;
}

.file-path {
  font-size: 12px;
  color: var(--text-muted);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.file-meta {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-shrink: 0;
}

.file-size {
  font-size: 12px;
  color: var(--text-muted);
}

.file-date {
  font-size: 12px;
  color: var(--text-muted);
}

.del-btn {
  width: 22px;
  height: 22px;
  border-radius: 50%;
  background: transparent;
  color: var(--text-muted);
  border: none;
  cursor: pointer;
  font-size: 16px;
  line-height: 1;
  display: flex;
  align-items: center;
  justify-content: center;
}

.del-btn:hover {
  background: rgba(220, 53, 69, 0.12);
  color: #dc3545;
}

.empty-state {
  text-align: center;
  padding: 40px;
  color: var(--text-muted);
  font-size: 14px;
}
</style>
