<template>
  <div class="video-page">
    <div class="page-header">
      <h1>视频下载与播放</h1>
      <p class="subtitle">下载 YouTube/Bilibili 等平台视频，本地播放</p>
    </div>

    <div class="help-banner">
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
        <circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/>
      </svg>
      <span>
        <b>使用流程：</b>
        1) 在「下载」页输入视频 URL，先点"查询信息"查看详情，再点"下载视频"
        2) 下载的视频保存在 <code>output/videos/</code> 目录
        3) 切换到「已下载」tab 查看和播放已下载的视频
        4) 「视频提取」用于从 HTML 页面中提取嵌入的视频链接，无需下载
      </span>
      <button class="help-close" @click="showHelp = false">×</button>
    </div>

    <!-- Tab 切换 -->
    <div class="tabs">
      <button :class="['tab', { active: activeTab === 'download' }]" @click="activeTab = 'download'">下载</button>
      <button :class="['tab', { active: activeTab === 'files' }]" @click="activeTab = 'files'; loadFiles()">已下载</button>
      <button :class="['tab', { active: activeTab === 'extract' }]" @click="activeTab = 'extract'">视频提取</button>
      <button :class="['tab', { active: activeTab === 'adremove' }]" @click="activeTab = 'adremove'">广告移除</button>
    </div>

    <!-- 下载 Tab -->
    <div v-if="activeTab === 'download'" class="tab-content">
      <div class="form-section">
        <label>视频 URL</label>
        <input v-model="downloadUrl" type="text" placeholder="https://www.youtube.com/watch?v=..." class="input-field" />
      </div>
      <div class="form-section">
        <label>标题（可选）</label>
        <input v-model="downloadTitle" type="text" placeholder="自定义文件名" class="input-field" />
      </div>
      <div class="form-row">
        <div class="form-section" style="flex:1">
          <label>Cookie 文件（可选，用于会员内容）</label>
          <input v-model="cookiesFile" type="text" placeholder="cookies.txt 路径" class="input-field" />
        </div>
        <div class="form-section" style="width:120px">
          <label>最大集数（0=全部）</label>
          <input v-model.number="maxItems" type="number" min="0" class="input-field" />
        </div>
      </div>
      <div class="button-row">
        <button @click="fetchInfo" :disabled="loading" class="btn btn-secondary">
          {{ loading ? '查询中...' : '查询信息' }}
        </button>
        <button @click="downloadVideo" :disabled="downloading" class="btn btn-primary">
          {{ downloading ? '下载中...' : '下载视频' }}
        </button>
      </div>

      <!-- 视频信息预览 -->
      <div v-if="videoInfo" class="info-card">
        <div v-if="videoInfo.thumbnail" class="thumbnail">
          <img :src="videoInfo.thumbnail" alt="thumbnail" />
        </div>
        <div class="info-body">
          <h3>{{ videoInfo.title || '未知标题' }}</h3>
          <p v-if="videoInfo.duration">时长: {{ formatDuration(videoInfo.duration) }}</p>
          <p v-if="videoInfo.uploader">作者: {{ videoInfo.uploader }}</p>
          <p v-if="videoInfo.is_playlist" class="playlist-badge">播放列表 · {{ videoInfo.playlist_count }} 集</p>
          <div v-if="videoInfo.formats && videoInfo.formats.length" class="formats">
            <p>可用格式: {{ videoInfo.formats.length }} 种</p>
          </div>
        </div>
      </div>

      <!-- 下载进度 -->
      <div v-if="downloadResult" class="result-card">
        <div class="result-header">
          <h3>下载结果</h3>
          <button v-if="downloadResult.downloaded > 0" class="btn btn-secondary btn-sm" @click="openVideoFolder">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/>
            </svg>
            打开视频文件夹
          </button>
        </div>
        <p>成功: {{ downloadResult.downloaded ?? 0 }} / {{ downloadResult.total ?? '?' }}</p>
        <p v-if="downloadResult.failed > 0" class="error-text">失败: {{ downloadResult.failed }}</p>
        <p v-if="downloadResult.error" class="error-text">错误: {{ downloadResult.error }}</p>
        <div v-for="item in downloadResult.items" :key="item.index" class="download-item">
          <span class="item-index">#{{ item.index }}</span>
          <div class="item-info">
            <div class="item-title">{{ item.title }}</div>
            <div v-if="item.path" class="item-path">{{ item.path }}</div>
          </div>
          <span :class="['item-status', item.success ? 'success' : 'failed']">
            {{ item.success ? '✓' : '✗' }}
          </span>
          <span v-if="item.size" class="item-size">{{ formatSize(item.size) }}</span>
          <span v-if="item.error" class="item-error">{{ item.error }}</span>
        </div>
      </div>
    </div>

    <!-- 已下载文件 Tab -->
    <div v-if="activeTab === 'files'" class="tab-content">
      <div class="files-toolbar">
        <button @click="loadFiles" class="btn btn-secondary">刷新</button>
        <button v-if="files.length > 0" @click="openVideoFolder" class="btn btn-secondary">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/>
          </svg>
          打开视频文件夹
        </button>
        <span v-if="files.length > 0" class="file-count">共 {{ files.length }} 个视频文件</span>
      </div>
      <div v-if="files.length === 0" class="empty">
        <p>暂无已下载的视频文件</p>
        <p class="empty-hint">去「下载」tab 输入视频 URL 开始下载</p>
      </div>
      <div v-for="file in files" :key="file.path" class="file-item" @click="playVideo(file)">
        <div class="file-icon">
          <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <polygon points="23 7 16 12 23 17 23 7"/>
            <rect x="1" y="5" width="15" height="14" rx="2" ry="2"/>
          </svg>
        </div>
        <div class="file-info">
          <div class="file-name">{{ file.name }}</div>
          <div class="file-meta">{{ formatSize(file.size) }} · {{ file.ext.toUpperCase() }} · {{ formatDate(file.modified) }}</div>
          <div class="file-path-display" :title="file.path">{{ file.path }}</div>
        </div>
        <div class="file-actions">
          <button @click.stop="playVideo(file)" class="btn btn-primary btn-sm">播放</button>
          <button @click.stop="openFileFolder(file.path)" class="btn btn-secondary btn-sm" title="打开所在文件夹">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/>
            </svg>
          </button>
          <button @click.stop="deleteFile(file)" class="btn btn-danger btn-sm">删除</button>
        </div>
      </div>

      <!-- 播放器 -->
      <div v-if="playingFile" class="player-modal" @click.self="playingFile = null">
        <div class="player-container">
          <video
            ref="videoPlayer"
            :src="`/api/video/stream?file_path=${encodeURIComponent(playingFile.path)}`"
            controls
            autoplay
            class="video-player"
          />
          <div class="player-info">
            <span>{{ playingFile.name }}</span>
            <button @click="playingFile = null" class="btn btn-secondary btn-sm">关闭</button>
          </div>
        </div>
      </div>
    </div>

    <!-- 视频提取 Tab -->
    <div v-if="activeTab === 'extract'" class="tab-content">
      <div class="form-section">
        <label>页面 URL（用于拼接相对路径）</label>
        <input v-model="extractBaseUrl" type="text" placeholder="https://example.com/page" class="input-field" />
      </div>
      <div class="form-section">
        <label>HTML 内容</label>
        <textarea v-model="extractHtml" rows="8" placeholder="<video src='...'></video>" class="input-field textarea"></textarea>
      </div>
      <button @click="extractVideos" :disabled="extracting" class="btn btn-primary">
        {{ extracting ? '提取中...' : '提取视频 URL' }}
      </button>

      <div v-if="extractResult" class="result-card">
        <h3>提取结果（{{ extractResult.total_count }} 个）</h3>
        <div v-if="extractResult.videos.length">
          <h4>视频标签</h4>
          <div v-for="(v, i) in extractResult.videos" :key="i" class="extract-item">
            <span class="badge">{{ v.type }}</span>
            <span class="badge">{{ v.source }}</span>
            <a :href="v.url" target="_blank">{{ v.url }}</a>
          </div>
        </div>
        <div v-if="extractResult.iframes.length">
          <h4>嵌入视频</h4>
          <div v-for="(v, i) in extractResult.iframes" :key="i" class="extract-item">
            <span class="badge">{{ v.platform }}</span>
            <a :href="v.url" target="_blank">{{ v.url }}</a>
          </div>
        </div>
        <div v-if="extractResult.m3u8_urls.length">
          <h4>M3U8 直链</h4>
          <div v-for="(url, i) in extractResult.m3u8_urls" :key="i" class="extract-item">
            <a :href="url" target="_blank">{{ url }}</a>
          </div>
        </div>
        <div v-if="extractResult.mp4_urls.length">
          <h4>MP4 直链</h4>
          <div v-for="(url, i) in extractResult.mp4_urls" :key="i" class="extract-item">
            <a :href="url" target="_blank">{{ url }}</a>
          </div>
        </div>
      </div>
    </div>

    <!-- 广告移除 Tab -->
    <div v-if="activeTab === 'adremove'" class="tab-content">
      <div class="form-section">
        <label>HTML 内容</label>
        <textarea v-model="adRemoveHtml" rows="8" placeholder="<html>...</html>" class="input-field textarea"></textarea>
      </div>
      <button @click="removeAds" :disabled="removingAds" class="btn btn-primary">
        {{ removingAds ? '清理中...' : '移除广告' }}
      </button>
      <div v-if="cleanedHtml" class="result-card">
        <h3>清理结果</h3>
        <textarea :value="cleanedHtml" rows="10" readonly class="input-field textarea"></textarea>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import axios from 'axios'

const api = axios.create({ baseURL: '/api' })

const showHelp = ref(true)
const activeTab = ref('download')

// 下载
const downloadUrl = ref('')
const downloadTitle = ref('')
const cookiesFile = ref('')
const maxItems = ref(0)
const loading = ref(false)
const downloading = ref(false)
const videoInfo = ref(null)
const downloadResult = ref(null)

// 文件列表
const files = ref([])
const playingFile = ref(null)

// 提取
const extractBaseUrl = ref('')
const extractHtml = ref('')
const extracting = ref(false)
const extractResult = ref(null)

// 广告移除
const adRemoveHtml = ref('')
const removingAds = ref(false)
const cleanedHtml = ref('')

async function fetchInfo() {
  if (!downloadUrl.value) return
  loading.value = true
  videoInfo.value = null
  try {
    const { data } = await api.post('/video/info', {
      url: downloadUrl.value,
      cookies_file: cookiesFile.value,
    })
    if (data.success) {
      videoInfo.value = data
    } else {
      alert('查询失败: ' + (data.error || '未知错误'))
    }
  } catch (e) {
    alert('请求失败: ' + e.message)
  } finally {
    loading.value = false
  }
}

async function downloadVideo() {
  if (!downloadUrl.value) return
  downloading.value = true
  downloadResult.value = null
  try {
    const { data } = await api.post('/video/download', {
      url: downloadUrl.value,
      title: downloadTitle.value,
      cookies_file: cookiesFile.value,
      max_items: maxItems.value,
    }, { timeout: 600000 })
    downloadResult.value = data
    if (data.downloaded > 0) {
      loadFiles()
    }
  } catch (e) {
    alert('下载失败: ' + e.message)
  } finally {
    downloading.value = false
  }
}

async function loadFiles() {
  try {
    const { data } = await api.get('/video/files')
    files.value = data.files || []
  } catch (e) {
    console.error('加载文件列表失败:', e)
  }
}

function playVideo(file) {
  playingFile.value = file
}

async function deleteFile(file) {
  if (!confirm(`确认删除 ${file.name}?`)) return
  try {
    await api.delete('/video/files', { params: { file_path: file.path } })
    loadFiles()
  } catch (e) {
    alert('删除失败: ' + e.message)
  }
}

async function extractVideos() {
  if (!extractHtml.value) return
  extracting.value = true
  extractResult.value = null
  try {
    const { data } = await api.post('/video/extract', {
      html: extractHtml.value,
      base_url: extractBaseUrl.value,
    })
    extractResult.value = data
  } catch (e) {
    alert('提取失败: ' + e.message)
  } finally {
    extracting.value = false
  }
}

async function removeAds() {
  if (!adRemoveHtml.value) return
  removingAds.value = true
  cleanedHtml.value = ''
  try {
    const { data } = await api.post('/video/ad-remove', {
      html: adRemoveHtml.value,
    })
    cleanedHtml.value = data.cleaned_html
  } catch (e) {
    alert('移除失败: ' + e.message)
  } finally {
    removingAds.value = false
  }
}

async function openVideoFolder() {
  try {
    await api.post('/output/open', null, { params: { path: './output/videos' } })
  } catch (e) {
    alert(`无法打开文件夹: ${e.response?.data?.detail || e.message}`)
  }
}

async function openFileFolder(filePath) {
  const separator = filePath.includes('\\') ? '\\' : '/'
  const lastSep = filePath.lastIndexOf(separator)
  const folderPath = lastSep > 0 ? filePath.substring(0, lastSep) : filePath
  try {
    await api.post('/output/open', null, { params: { path: folderPath } })
  } catch (e) {
    alert(`无法打开文件夹: ${e.response?.data?.detail || e.message}`)
  }
}

function formatSize(bytes) {
  if (!bytes || isNaN(bytes) || bytes < 0) return ''
  if (bytes < 1024) return bytes + ' B'
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB'
  if (bytes < 1024 * 1024 * 1024) return (bytes / (1024 * 1024)).toFixed(1) + ' MB'
  return (bytes / (1024 * 1024 * 1024)).toFixed(2) + ' GB'
}

function formatDuration(seconds) {
  if (!seconds) return ''
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  const s = seconds % 60
  if (h > 0) return `${h}:${String(m).padStart(2,'0')}:${String(s).padStart(2,'0')}`
  return `${m}:${String(s).padStart(2,'0')}`
}

function formatDate(ts) {
  return new Date(ts * 1000).toLocaleString('zh-CN')
}
</script>

<style scoped>
.video-page {
  padding: 24px;
  max-width: 960px;
  margin: 0 auto;
}
.page-header h1 {
  margin: 0 0 4px 0;
  font-size: 24px;
}
.subtitle {
  color: var(--text-muted);
  font-size: 14px;
  margin: 0 0 16px 0;
}
.help-banner {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 12px 16px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 8px;
  margin-bottom: 20px;
  font-size: 13px;
  line-height: 1.6;
}
.help-banner svg {
  flex-shrink: 0;
  margin-top: 2px;
  color: var(--accent);
}
.help-banner b {
  color: var(--text);
}
.help-banner code {
  background: var(--bg);
  padding: 1px 6px;
  border-radius: 4px;
  font-family: monospace;
  font-size: 12px;
}
.help-close {
  margin-left: auto;
  background: none;
  border: none;
  color: var(--text-muted);
  cursor: pointer;
  font-size: 18px;
  line-height: 1;
  padding: 0 4px;
}
.help-close:hover {
  color: var(--text);
}
.tabs {
  display: flex;
  gap: 4px;
  border-bottom: 1px solid var(--border);
  margin-bottom: 24px;
}
.tab {
  padding: 8px 16px;
  background: none;
  border: none;
  color: var(--text-muted);
  cursor: pointer;
  border-bottom: 2px solid transparent;
  font-size: 14px;
}
.tab.active {
  color: var(--accent);
  border-bottom-color: var(--accent);
}
.tab-content {
  min-height: 300px;
}
.form-section {
  margin-bottom: 16px;
}
.form-section label {
  display: block;
  font-size: 13px;
  color: var(--text-muted);
  margin-bottom: 6px;
}
.form-row {
  display: flex;
  gap: 16px;
}
.input-field {
  width: 100%;
  padding: 8px 12px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 6px;
  color: var(--text);
  font-size: 14px;
  box-sizing: border-box;
}
.textarea {
  font-family: monospace;
  resize: vertical;
}
.button-row {
  display: flex;
  gap: 12px;
  margin-top: 16px;
}
.btn {
  padding: 8px 20px;
  border: none;
  border-radius: 6px;
  cursor: pointer;
  font-size: 14px;
}
.btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.btn-primary {
  background: var(--accent);
  color: white;
}
.btn-secondary {
  background: var(--surface);
  color: var(--text);
  border: 1px solid var(--border);
}
.btn-danger {
  background: #e53e3e;
  color: white;
}
.btn-sm {
  padding: 4px 12px;
  font-size: 12px;
}
.info-card {
  display: flex;
  gap: 16px;
  padding: 16px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 8px;
  margin-top: 16px;
}
.thumbnail img {
  width: 200px;
  border-radius: 4px;
}
.info-body h3 {
  margin: 0 0 8px 0;
}
.info-body p {
  margin: 4px 0;
  font-size: 13px;
  color: var(--text-muted);
}
.playlist-badge {
  display: inline-block;
  padding: 2px 8px;
  background: var(--accent);
  color: white;
  border-radius: 4px;
  font-size: 12px;
}
.result-card {
  padding: 16px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 8px;
  margin-top: 16px;
}
.result-card h3 {
  margin: 0 0 12px 0;
}
.download-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 0;
  border-bottom: 1px solid var(--border);
  font-size: 13px;
}
.item-index {
  color: var(--text-muted);
  width: 30px;
}
.item-title {
  flex: 1;
}
.item-status.success {
  color: #38a169;
}
.item-status.failed {
  color: #e53e3e;
}
.item-size {
  color: var(--text-muted);
}
.item-error {
  color: #e53e3e;
  font-size: 12px;
}
.empty {
  text-align: center;
  padding: 40px;
  color: var(--text-muted);
}
.file-item {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 6px;
  margin-bottom: 8px;
  cursor: pointer;
}
.file-item:hover {
  border-color: var(--accent);
}
.file-icon {
  color: var(--accent);
}
.file-info {
  flex: 1;
}
.file-name {
  font-size: 14px;
}
.file-meta {
  font-size: 12px;
  color: var(--text-muted);
}
.player-modal {
  position: fixed;
  top: 0;
  left: 0;
  width: 100%;
  height: 100%;
  background: rgba(0,0,0,0.8);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
}
.player-container {
  background: var(--surface);
  border-radius: 8px;
  padding: 16px;
  max-width: 90%;
  max-height: 90%;
}
.video-player {
  max-width: 800px;
  width: 100%;
  border-radius: 4px;
}
.player-info {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-top: 8px;
  font-size: 13px;
}
.extract-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 4px 0;
  font-size: 13px;
  word-break: break-all;
}
.badge {
  padding: 2px 8px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 4px;
  font-size: 11px;
  white-space: nowrap;
}
.error-text {
  color: #e53e3e;
}
.result-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
}
.result-header h3 {
  margin: 0;
}
.files-toolbar {
  display: flex;
  gap: 12px;
  align-items: center;
  margin-bottom: 16px;
}
.file-count {
  color: var(--text-muted);
  font-size: 13px;
}
.empty-hint {
  color: var(--text-muted);
  font-size: 13px;
  margin-top: 8px;
}
.file-path-display {
  font-size: 11px;
  color: var(--text-muted);
  font-family: monospace;
  margin-top: 4px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 400px;
}
.file-actions {
  display: flex;
  gap: 6px;
  flex-shrink: 0;
}
.item-info {
  flex: 1;
  min-width: 0;
}
.item-path {
  font-size: 11px;
  color: var(--text-muted);
  font-family: monospace;
  margin-top: 2px;
  word-break: break-all;
}
</style>
