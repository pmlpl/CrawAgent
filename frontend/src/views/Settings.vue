<template>
  <div class="settings-page">
    <header class="page-header">
      <h2 class="font-display">设置</h2>
    </header>

    <div class="settings-content">
      <div class="settings-section">
        <div class="section-title">服务状态</div>
        <div class="status-grid">
          <div class="status-item">
            <span class="status-label">后端服务</span>
            <span class="status-value" :class="serverOnline ? 'ok' : 'err'">
              {{ serverOnline ? '在线' : '离线' }}
            </span>
          </div>
          <div class="status-item">
            <span class="status-label">数据库</span>
            <span class="status-value" :class="dbOnline ? 'ok' : 'err'">
              {{ dbOnline ? '已连接' : '未连接' }}
            </span>
          </div>
          <div class="status-item">
            <span class="status-label">Mock 模式</span>
            <span class="status-value">{{ mockMode ? '开启' : '关闭' }}</span>
          </div>
        </div>
      </div>

      <div class="settings-section">
        <div class="section-title">快速爬取</div>
        <p class="section-desc">不需要对话，直接输入网址快速爬取内容</p>
        <div class="quick-form">
          <input
            v-model="quickUrl"
            class="quick-input"
            placeholder="https://example.com"
          />
          <input
            v-model="quickInstr"
            class="quick-input"
            placeholder="提取要求（可选，如：获取标题和正文）"
          />
          <button class="primary-btn" :disabled="!quickUrl || quickLoading" @click="quickCrawl">
            {{ quickLoading ? '爬取中…' : '开始爬取' }}
          </button>
        </div>
        <div class="quick-result" v-if="quickResult">
          <pre>{{ quickResult }}</pre>
        </div>
      </div>

      <div class="settings-section">
        <div class="section-title">关于</div>
        <div class="about-info">
          <div class="about-row">
            <span class="about-label">版本</span>
            <span class="about-value">v2.0 (Agent Harness)</span>
          </div>
          <div class="about-row">
            <span class="about-label">架构</span>
            <span class="about-value">CrawlHarness + CrawlLoop + Hooks</span>
          </div>
          <div class="about-row">
            <span class="about-label">技术栈</span>
            <span class="about-value">Python · FastAPI · Vue 3 · MySQL · Redis</span>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { systemApi, harnessApi } from '../api'

const serverOnline = ref(false)
const dbOnline = ref(false)
const mockMode = ref(false)
const quickUrl = ref('')
const quickInstr = ref('')
const quickResult = ref('')
const quickLoading = ref(false)

onMounted(async () => {
  try {
    const res = await systemApi.health()
    serverOnline.value = true
    dbOnline.value = res.database || false
    mockMode.value = res.mock_mode || false
  } catch {
    serverOnline.value = false
  }
})

async function quickCrawl() {
  if (!quickUrl.value) return
  quickLoading.value = true
  quickResult.value = ''
  try {
    const res = await harnessApi.quickCrawl(quickUrl.value, quickInstr.value)
    quickResult.value = JSON.stringify(res, null, 2)
  } catch (e) {
    quickResult.value = `错误: ${e.response?.data?.detail || e.message}`
  } finally {
    quickLoading.value = false
  }
}
</script>

<style scoped>
.settings-page {
  display: flex;
  flex-direction: column;
  height: 100%;
  background: var(--paper);
}

.page-header {
  padding: 16px 28px;
  border-bottom: 1px solid var(--line);
  flex-shrink: 0;
}

.page-header h2 {
  font-size: 18px;
  color: var(--ink);
}

.settings-content {
  flex: 1;
  overflow-y: auto;
  padding: 24px 28px;
  max-width: 760px;
  margin: 0 auto;
  width: 100%;
}

.settings-section {
  margin-bottom: 32px;
}

.section-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--ink);
  margin-bottom: 12px;
}

.section-desc {
  font-size: 13px;
  color: var(--ink-soft);
  margin-bottom: 12px;
}

.status-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 12px;
}

.status-item {
  background: var(--white);
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
  padding: 14px 16px;
}

.status-label {
  display: block;
  font-size: 12px;
  color: var(--ink-faint);
  margin-bottom: 4px;
}

.status-value {
  font-size: 14px;
  font-weight: 600;
  color: var(--ink);
}

.status-value.ok {
  color: var(--moss);
}

.status-value.err {
  color: var(--clay);
}

.quick-form {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.quick-input {
  padding: 10px 14px;
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
  font-size: 13px;
  font-family: var(--font-body);
  background: var(--white);
  outline: none;
  transition: border-color 0.2s var(--ease);
}

.quick-input:focus {
  border-color: var(--terracotta);
  box-shadow: 0 0 0 3px var(--terracotta-soft);
}

.primary-btn {
  align-self: flex-start;
  padding: 10px 24px;
  background: var(--terracotta);
  color: var(--white);
  border: none;
  border-radius: var(--radius-md);
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.2s var(--ease);
}

.primary-btn:hover:not(:disabled) {
  background: var(--terracotta-dark);
}

.primary-btn:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

.quick-result {
  margin-top: 12px;
  background: #1C1917;
  color: #F5F1EA;
  border-radius: var(--radius-md);
  padding: 14px 16px;
  overflow-x: auto;
}

.quick-result pre {
  font-size: 12px;
  line-height: 1.5;
  white-space: pre-wrap;
  word-wrap: break-word;
}

.about-info {
  background: var(--white);
  border: 1px solid var(--line);
  border-radius: var(--radius-md);
  padding: 4px 16px;
}

.about-row {
  display: flex;
  justify-content: space-between;
  padding: 12px 0;
  border-bottom: 1px solid var(--line);
}

.about-row:last-child {
  border-bottom: none;
}

.about-label {
  font-size: 13px;
  color: var(--ink-soft);
}

.about-value {
  font-size: 13px;
  color: var(--ink);
  font-weight: 500;
}

@media (max-width: 640px) {
  .status-grid {
    grid-template-columns: 1fr;
  }
}
</style>
