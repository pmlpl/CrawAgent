<template>
  <div class="output-page">
    <header class="page-header">
      <h2 class="font-display">文件整理</h2>
      <button class="ghost-btn" @click="loadFiles" :disabled="filesLoading">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <path d="M23 4v6h-6M1 20v-6h6"/>
          <path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/>
        </svg>
        刷新
      </button>
    </header>

    <div class="help-banner">
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
        <circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/>
      </svg>
      <span>这里查看所有爬取保存的文件，双击文件用系统默认程序打开，点击路径旁的图标直接跳转到所在文件夹。</span>
    </div>

    <div class="files-toolbar" v-if="files.length">
      <span class="count-label">共 {{ files.length }} 个文件</span>
      <div class="toolbar-right">
        <select v-model="filterExt" class="filter-select" @change="applyFilter">
          <option value="">全部类型</option>
          <option v-for="ext in extList" :key="ext" :value="ext">{{ ext.toUpperCase() }}</option>
        </select>
      </div>
    </div>

    <div class="files-content">
      <!-- 加载中 -->
      <div class="loading-state" v-if="filesLoading">
        <div class="typing-dots"><span></span><span></span><span></span></div>
        <p>加载文件列表…</p>
      </div>

      <!-- 空状态 -->
      <div class="empty-state" v-else-if="filteredFiles.length === 0">
        <svg width="56" height="56" viewBox="0 0 56 56" fill="none">
          <path d="M8 14C8 11.79 9.79 10 12 10h9l4 4h19c2.21 0 4 1.79 4 4v28c0 2.21-1.79 4-4 4H12c-2.21 0-4-1.79-4-4V14z" stroke="var(--ink-faint)" stroke-width="2" fill="none"/>
          <path d="M8 20h44" stroke="var(--ink-faint)" stroke-width="2"/>
        </svg>
        <p class="empty-title">还没有保存的文件</p>
        <p class="empty-desc">去对话页面开始爬取，爬取结果会自动保存在这里。</p>
      </div>

      <!-- 文件列表 -->
      <div class="files-grid" v-else>
        <div
          v-for="f in filteredFiles"
          :key="f.path"
          class="file-card"
          @dblclick="openFile(f)"
          @click="selectFile(f)"
          :class="{ selected: currentFile?.path === f.path }"
        >
          <div class="file-icon" :class="'ext-' + getExt(f).toLowerCase()">
            {{ getExt(f).toUpperCase().slice(0, 4) }}
          </div>
          <div class="file-info">
            <div class="file-name" :title="f.name">{{ f.name }}</div>
            <div class="file-meta">
              <span>{{ formatSize(f.size) }}</span>
              <span>·</span>
              <span>{{ formatDate(f.modified) }}</span>
            </div>
            <div class="file-path-row">
              <span class="file-path" :title="f.relative_path">{{ f.relative_path }}</span>
              <button class="folder-btn" @click.stop="openFolder(f.path)" title="打开所在文件夹">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                  <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/>
                </svg>
              </button>
            </div>
          </div>
          <div class="file-actions" @click.stop>
            <button class="action-btn" @click="openFile(f)" title="打开文件">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M15 3h6v6M14 10l7-7M10 21H3v-7"/>
              </svg>
            </button>
            <button class="del-btn" @click="deleteFile(f)" title="删除">×</button>
          </div>
        </div>
      </div>
    </div>

    <!-- 文件预览 -->
    <div class="preview-panel" v-if="currentFile" @click.self="currentFile = null">
      <div class="preview-card">
        <div class="preview-header">
          <span class="preview-title">{{ currentFile.name }}</span>
          <div class="preview-actions">
            <button class="ghost-btn" @click="openFile(currentFile)">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M15 3h6v6M14 10l7-7M10 21H3v-7"/>
              </svg>
              用默认程序打开
            </button>
            <button class="ghost-btn" @click="currentFile = null">关闭</button>
          </div>
        </div>
        <div class="preview-body">
          <pre>{{ fileContent || '加载中…' }}</pre>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import axios from 'axios'

const api = axios.create({ baseURL: '/api', timeout: 30000 })

const files = ref([])
const filesLoading = ref(false)
const currentFile = ref(null)
const fileContent = ref('')
const filterExt = ref('')

const extList = computed(() => {
  const exts = new Set()
  files.value.forEach(f => { exts.add(getExt(f).toLowerCase()) })
  return [...exts].sort()
})

const filteredFiles = computed(() => {
  if (!filterExt.value) return files.value
  return files.value.filter(f => getExt(f).toLowerCase() === filterExt.value)
})

// 后端未返回 ext 字段时从文件名推导扩展名
function getExt(f) {
  if (f.ext) return f.ext
  const m = /\.([A-Za-z0-9]+)$/.exec(f.name || f.path || '')
  return m ? m[1] : 'file'
}

onMounted(() => loadFiles())

async function loadFiles() {
  filesLoading.value = true
  try {
    const res = await api.get('/output/files', { params: { limit: 500 } })
    files.value = res.data.files || []
  } catch (e) {
    console.error('加载文件列表失败:', e)
    files.value = []
  } finally {
    filesLoading.value = false
  }
}

function applyFilter() {}

function selectFile(f) {
  currentFile.value = f
  fileContent.value = '加载中…'
  loadFileContent(f)
}

async function loadFileContent(f) {
  try {
    const res = await api.get('/output/file', { params: { path: f.path } })
    fileContent.value = res.data.content
  } catch (e) {
    fileContent.value = `读取失败: ${e.response?.data?.detail || e.message}`
  }
}

async function openFile(f) {
  try {
    await api.post('/output/open', null, { params: { path: f.path } })
  } catch (e) {
    alert(`无法打开文件: ${e.response?.data?.detail || e.message}\n\n如果是远程服务器部署，需要手动下载后打开。`)
  }
}

async function openFolder(filePath) {
  const separator = filePath.includes('\\') ? '\\' : '/'
  const lastSep = filePath.lastIndexOf(separator)
  const folderPath = lastSep > 0 ? filePath.substring(0, lastSep) : filePath
  try {
    await api.post('/output/open', null, { params: { path: folderPath } })
  } catch (e) {
    alert(`无法打开文件夹: ${e.response?.data?.detail || e.message}`)
  }
}

async function deleteFile(f) {
  if (!confirm(`确认删除「${f.name}」？`)) return
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
  display: flex;
  flex-direction: column;
  height: 100%;
  background: var(--paper);
}

.page-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 16px 28px;
  border-bottom: 1px solid var(--line);
  flex-shrink: 0;
}

.page-header h2 {
  font-size: 18px;
  color: var(--ink);
}

.ghost-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 6px 14px;
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
  background: var(--white);
  color: var(--ink-soft);
  font-size: 13px;
  cursor: pointer;
  transition: all 0.2s var(--ease);
}

.ghost-btn:hover {
  border-color: var(--terracotta);
  color: var(--terracotta);
}

.ghost-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.help-banner {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 28px;
  background: var(--paper-warm);
  color: var(--ink-soft);
  font-size: 12px;
  border-bottom: 1px solid var(--line);
}

.help-banner svg {
  flex-shrink: 0;
  color: var(--terracotta);
}

.files-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 28px;
  border-bottom: 1px solid var(--line);
  flex-shrink: 0;
}

.count-label {
  font-size: 13px;
  color: var(--ink-faint);
}

.toolbar-right {
  display: flex;
  gap: 8px;
}

.filter-select {
  padding: 5px 10px;
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
  background: var(--white);
  font-size: 12px;
  color: var(--ink-soft);
  outline: none;
}

.filter-select:focus {
  border-color: var(--terracotta);
}

.files-content {
  flex: 1;
  overflow-y: auto;
  padding: 16px 28px;
}

.loading-state, .empty-state {
  text-align: center;
  padding: 60px 20px;
  color: var(--ink-faint);
}

.empty-title {
  font-size: 16px;
  color: var(--ink-soft);
  margin: 16px 0 4px;
}

.empty-desc {
  font-size: 13px;
}

.files-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(360px, 1fr));
  gap: 10px;
}

.file-card {
  display: flex;
  gap: 12px;
  align-items: flex-start;
  padding: 12px 14px;
  background: var(--white);
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
  cursor: pointer;
  transition: all 0.15s var(--ease);
}

.file-card:hover {
  border-color: var(--terracotta-soft);
  box-shadow: var(--shadow-sm);
}

.file-card.selected {
  border-color: var(--terracotta);
  background: var(--paper-warm);
}

.file-icon {
  width: 42px;
  height: 42px;
  border-radius: var(--radius-sm);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 10px;
  font-weight: 600;
  color: var(--ink-faint);
  background: var(--paper-warm);
  flex-shrink: 0;
  letter-spacing: 0.5px;
}

.file-icon.ext-md,
.file-icon.ext-txt,
.file-icon.ext-json {
  background: var(--moss-soft);
  color: var(--moss);
}

.file-icon.ext-html,
.file-icon.ext-css {
  background: var(--terracotta-soft);
  color: var(--terracotta);
}

.file-icon.ext-pdf,
.file-icon.ext-doc {
  background: #fde2e2;
  color: #d45454;
}

.file-icon.ext-png,
.file-icon.ext-jpg,
.file-icon.ext-jpeg,
.file-icon.ext-gif {
  background: #e2d9f3;
  color: #7c3aed;
}

.file-icon.ext-mp4,
.file-icon.ext-webm,
.file-icon.ext-mkv {
  background: #fce7f3;
  color: #db2777;
}

.file-info {
  flex: 1;
  min-width: 0;
}

.file-name {
  font-size: 13px;
  font-weight: 600;
  color: var(--ink);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  margin-bottom: 3px;
}

.file-meta {
  font-size: 11px;
  color: var(--ink-faint);
  display: flex;
  gap: 4px;
  margin-bottom: 4px;
}

.file-path-row {
  display: flex;
  align-items: center;
  gap: 4px;
  background: var(--paper);
  border-radius: var(--radius-sm);
  padding: 3px 6px;
  border: 1px solid var(--line);
}

.file-path {
  flex: 1;
  font-size: 11px;
  color: var(--ink-faint);
  font-family: monospace;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.folder-btn {
  width: 20px;
  height: 20px;
  border: none;
  background: transparent;
  color: var(--ink-faint);
  cursor: pointer;
  border-radius: 3px;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  transition: all 0.15s var(--ease);
}

.folder-btn:hover {
  background: var(--terracotta-soft);
  color: var(--terracotta);
}

.file-actions {
  display: flex;
  gap: 4px;
  flex-shrink: 0;
}

.action-btn {
  width: 26px;
  height: 26px;
  border: none;
  border-radius: 50%;
  background: var(--paper);
  color: var(--ink-faint);
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all 0.15s var(--ease);
}

.action-btn:hover {
  background: var(--terracotta-soft);
  color: var(--terracotta);
}

.del-btn {
  width: 22px;
  height: 22px;
  border-radius: 50%;
  background: transparent;
  color: var(--ink-faint);
  border: none;
  cursor: pointer;
  font-size: 14px;
  line-height: 1;
  display: flex;
  align-items: center;
  justify-content: center;
}

.del-btn:hover {
  background: rgba(220, 53, 69, 0.12);
  color: #dc3545;
}

.preview-panel {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.4);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
  padding: 40px;
}

.preview-card {
  background: var(--white);
  border-radius: var(--radius-lg);
  width: 100%;
  max-width: 900px;
  max-height: 80vh;
  display: flex;
  flex-direction: column;
  box-shadow: var(--shadow-lg);
}

.preview-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 14px 20px;
  border-bottom: 1px solid var(--line);
}

.preview-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--ink);
}

.preview-actions {
  display: flex;
  gap: 8px;
}

.preview-body {
  flex: 1;
  overflow-y: auto;
  padding: 20px;
}

.preview-body pre {
  margin: 0;
  font-size: 13px;
  line-height: 1.6;
  color: var(--ink);
  white-space: pre-wrap;
  word-break: break-word;
}

.typing-dots {
  display: inline-flex;
  gap: 4px;
  justify-content: center;
  margin-bottom: 12px;
}

.typing-dots span {
  width: 6px;
  height: 6px;
  background: var(--terracotta);
  border-radius: 50%;
  animation: bounce 1.4s infinite ease-in-out both;
}

.typing-dots span:nth-child(1) { animation-delay: -0.32s; }
.typing-dots span:nth-child(2) { animation-delay: -0.16s; }

@keyframes bounce {
  0%, 80%, 100% { transform: scale(0); }
  40% { transform: scale(1); }
}

@media (max-width: 768px) {
  .files-grid {
    grid-template-columns: 1fr;
  }
  .file-path-row {
    display: none;
  }
}
</style>
