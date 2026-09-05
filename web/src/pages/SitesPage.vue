<script setup>
// 站点管理页（列表）
// 数据来源：后端 /api/sites（与 Agent 的 save_site_profile 工具共享 data/sites.json）
// 点击站点行 → 进入 /sites-detail/:origin 详情页查看 / 编辑 / 复抓
// 列表本身只负责：加载、一键复抓、删除、跳转详情
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useChat } from '../composables/useChat'

const router = useRouter()
const chat = useChat()
const sites = ref([])
const loading = ref(false)
const error = ref('')

function openDetail(site) {
  router.push({ name: 'site-detail', params: { origin: site.origin } })
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const res = await fetch('/api/sites')
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const data = await res.json()
    sites.value = data.sites || []
  } catch (e) {
    error.value = String(e?.message || e)
  } finally {
    loading.value = false
  }
}

async function onDelete(site, ev) {
  ev?.stopPropagation?.()
  if (!window.confirm(`删除站点档案 ${site.origin}？此操作不可恢复。`)) return
  try {
    await fetch(`/api/sites/${encodeURIComponent(site.origin)}`, { method: 'DELETE' })
    await load()
  } catch (e) {
    error.value = '删除失败：' + (e?.message || e)
  }
}

function onRecrawl(site, ev) {
  ev?.stopPropagation?.()
  const prompt = `请重新抓取 ${site.origin}（${site.title || ''}）的最新内容。`
  router.push('/chat')
  setTimeout(() => {
    if (chat.connected.value) {
      chat.send(prompt)
    } else {
      chat.lastDraft.value = prompt
    }
  }, 300)
}

function backToChat() {
  router.push('/chat')
}

const strategyLabels = {
  crawl_webpage: '静态 HTML',
  browse_and_crawl: '浏览器渲染',
  weread_cookie: '微信读书 Cookie',
  wallpaper: '壁纸图片站',
  social_media: '社媒下载',
}

onMounted(load)
</script>

<template>
  <div class="page">
    <div class="page-head">
      <div>
        <h1>站点档案</h1>
        <p class="muted">
          每个站点首次抓取时生成档案 · 之后由 Agent 复用策略跳过分析阶段
        </p>
      </div>
      <button class="btn" type="button" @click="load" :disabled="loading">
        {{ loading ? '刷新中…' : '刷新' }}
      </button>
    </div>

    <div v-if="error" class="err-tip">加载失败：{{ error }}</div>

    <div v-if="!loading && !sites.length && !error" class="empty">
      <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.4" aria-hidden="true">
        <circle cx="12" cy="12" r="10" />
        <line x1="2" y1="12" x2="22" y2="12" />
        <path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z" />
      </svg>
      <h2>暂无站点档案</h2>
      <p class="muted">
        在对话页让智能体抓取某个网站，<br />
        智能体首次会详细分析站点结构并保存为档案，<br />
        之后该站点会出现在这里，点击「查看详情」可进入档案详情。
      </p>
      <button class="btn" type="button" @click="backToChat">前往对话页</button>
    </div>

    <ul v-else-if="sites.length" class="site-list">
      <li
        v-for="s in sites"
        :key="s.origin"
        class="site-item"
        role="button"
        tabindex="0"
        @click="openDetail(s)"
        @keydown.enter="openDetail(s)"
      >
        <div class="site-info">
          <div class="site-row">
            <span class="site-origin">{{ s.origin }}</span>
            <span v-if="s.strategy" class="site-strategy">
              {{ strategyLabels[s.strategy] || s.strategy }}
            </span>
            <span v-if="s.cookies" class="cookie-chip">Cookie 已配置</span>
          </div>
          <span v-if="s.title" class="site-title">{{ s.title }}</span>
          <span v-if="s.notes" class="site-notes">{{ s.notes }}</span>
          <span v-if="s.last_crawled_at" class="site-time">
            上次抓取 · {{ s.last_crawled_at.replace('T', ' ') }}
          </span>
        </div>
        <div class="site-actions" @click.stop>
          <button class="btn btn-recrawl" type="button" @click="onRecrawl(s, $event)">
            一键复抓
          </button>
          <button class="btn btn-detail" type="button" @click="openDetail(s)">查看详情</button>
          <button class="btn-icon-del" type="button" title="删除档案" @click="onDelete(s, $event)">
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="3 6 5 6 21 6" /><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" /></svg>
          </button>
        </div>
      </li>
    </ul>
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
  gap: 20px;
}

.page-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}
.page-head h1 {
  font-family: var(--font-display);
  font-size: 22px;
  font-weight: 700;
  margin: 0 0 4px;
}
.muted { color: var(--dim); font-size: 13.5px; line-height: 1.6; }

.err-tip {
  padding: 10px 14px;
  background: var(--danger-soft);
  color: var(--danger);
  border: 1px solid color-mix(in srgb, var(--danger) 35%, transparent);
  border-radius: 8px;
  font-size: 13px;
}

.empty {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  text-align: center;
  gap: 14px;
  padding: 60px 20px;
  color: var(--dim);
  border: 1px dashed var(--line);
  border-radius: var(--radius);
}
.empty h2 { font-size: 17px; color: var(--ink); margin: 4px 0 0; }
.empty .btn { margin-top: 8px; }

.site-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.site-item {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 14px 16px;
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: var(--radius);
  box-shadow: var(--shadow);
  cursor: pointer;
  transition: border-color .15s, transform .1s;
}
.site-item:hover { border-color: var(--accent); }
.site-item:focus { outline: none; border-color: var(--accent); box-shadow: 0 0 0 3px color-mix(in srgb, var(--accent) 25%, transparent); }

.site-info { display: flex; flex-direction: column; gap: 3px; min-width: 0; flex: 1; }
.site-row { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.site-origin {
  font-family: var(--font-mono);
  font-size: 14px;
  color: var(--accent);
  font-weight: 600;
  overflow-wrap: anywhere;
  word-break: break-word;
}
.site-strategy {
  font-size: 11px;
  font-family: var(--font-mono);
  padding: 2px 8px;
  border-radius: 999px;
  background: var(--accent-soft);
  color: var(--accent);
  white-space: nowrap;
}
.site-title { font-size: 13.5px; color: var(--ink); }
.site-notes {
  font-size: 12px;
  color: var(--dim);
  display: -webkit-box;
  -webkit-line-clamp: 1;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.site-time { font-size: 12px; color: var(--faint); }

.site-actions { display: flex; gap: 8px; align-items: center; flex: none; }

.btn {
  appearance: none;
  border: 1px solid var(--line);
  border-radius: 9px;
  height: 36px;
  padding: 0 14px;
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  gap: 8px;
  transition: border-color .15s, background .15s, filter .15s, transform .1s;
}
.btn:active { transform: scale(.97); }
.btn-recrawl {
  background: var(--accent);
  color: var(--accent-ink);
  border-color: var(--accent);
}
.btn-recrawl:hover { filter: brightness(1.06); }
.btn-detail {
  background: var(--panel-2);
  color: var(--dim);
}
.btn-detail:hover { color: var(--accent); border-color: var(--accent); }

.cookie-chip {
  font-size: 11px;
  font-family: var(--font-mono);
  padding: 2px 8px;
  border-radius: 999px;
  background: color-mix(in srgb, var(--accent) 12%, transparent);
  color: var(--accent);
  white-space: nowrap;
}

.btn-icon-del {
  appearance: none;
  border: 1px solid var(--line);
  background: var(--panel-2);
  color: var(--faint);
  width: 36px;
  height: 36px;
  border-radius: 9px;
  cursor: pointer;
  display: grid;
  place-items: center;
  transition: color .15s, border-color .15s, background .15s;
}
.btn-icon-del:hover { color: var(--danger); border-color: var(--danger); background: var(--danger-soft); }

@media (max-width: 640px) {
  .page { padding: 20px 14px 24px; }
  .site-item { flex-direction: column; align-items: stretch; }
  .site-actions { justify-content: flex-end; flex-wrap: wrap; }
}
</style>
