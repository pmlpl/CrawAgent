<script setup>
// 站点档案详情页 — 在 /sites 列表中点击站点后跳到这里
// 来源：/api/sites 全量数据，根据当前 :origin 查找并展示所有字段
// 操作：编辑 + 保存（除 origin 外）、一键复抓、删除
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useChat } from '../composables/useChat'

const route = useRoute()
const router = useRouter()
const chat = useChat()

const site = ref(null)
const loading = ref(false)
const error = ref('')
const saving = ref(false)
const saveTip = ref('')

// 编辑表单（origin 不可改）
const editForm = ref({ title: '', strategy: '', notes: '', cookies: '' })
const editing = ref(false)

const strategyOptions = [
  { value: 'crawl_webpage', label: '静态 HTML' },
  { value: 'browse_and_crawl', label: '浏览器渲染' },
  { value: 'weread_cookie', label: '微信读书 Cookie' },
  { value: 'wallpaper', label: '壁纸图片站' },
  { value: 'social_media', label: '社媒下载' },
  { value: 'custom_script', label: '自定义脚本' },
]
const strategyLabel = (v) => strategyOptions.find(x => x.value === v)?.label || v || '未指定'

function fmtDate(s) {
  if (!s) return '—'
  return String(s).replace('T', ' ')
}

const originKey = computed(() => decodeURIComponent(String(route.params.origin || '')))

async function load() {
  loading.value = true
  error.value = ''
  site.value = null
  try {
    const res = await fetch('/api/sites')
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const data = await res.json()
    const found = (data.sites || []).find(s => s.origin === originKey.value)
    if (!found) {
      error.value = `未找到 origin = ${originKey.value} 的站点档案`
      return
    }
    site.value = found
    syncForm()
  } catch (e) {
    error.value = String(e?.message || e)
  } finally {
    loading.value = false
  }
}

function syncForm() {
  if (!site.value) return
  editForm.value = {
    title: site.value.title || '',
    strategy: site.value.strategy || '',
    notes: site.value.notes || '',
    cookies: '', // cookie 输入默认留空，避免误覆盖
  }
}

function startEdit() {
  syncForm()
  editing.value = true
  saveTip.value = ''
}

function cancelEdit() {
  editing.value = false
  saveTip.value = ''
}

async function save() {
  if (!site.value) return
  saving.value = true
  saveTip.value = ''
  try {
    const body = {
      origin: site.value.origin,
      title: editForm.value.title.trim(),
      strategy: editForm.value.strategy.trim(),
      notes: editForm.value.notes,
    }
    // 仅当用户键入了 cookie 时才提交，否则保持原值不变
    const c = editForm.value.cookies.trim()
    if (c) body.cookies = c
    const res = await fetch('/api/sites', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    const data = await res.json()
    if (!data.ok) throw new Error(data.error || '保存失败')
    site.value = data.site
    editing.value = false
    editForm.value.cookies = ''
    saveTip.value = '已保存'
  } catch (e) {
    saveTip.value = '保存失败：' + (e?.message || e)
  } finally {
    saving.value = false
  }
}

async function clearCookies() {
  if (!window.confirm(`清除 ${originKey.value} 的已存 Cookie？此操作不可恢复。`)) return
  saving.value = true
  saveTip.value = ''
  try {
    const res = await fetch('/api/sites', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ origin: originKey.value, cookies: '' }),
    })
    const data = await res.json()
    if (!data.ok) throw new Error(data.error || '清除失败')
    site.value = data.site
    saveTip.value = 'Cookie 已清除'
  } catch (e) {
    saveTip.value = '清除失败：' + (e?.message || e)
  } finally {
    saving.value = false
  }
}

async function remove() {
  if (!window.confirm(`删除站点档案 ${originKey.value}？此操作不可恢复。`)) return
  try {
    await fetch(`/api/sites/${encodeURIComponent(originKey.value)}`, { method: 'DELETE' })
    router.replace('/sites')
  } catch (e) {
    error.value = '删除失败：' + (e?.message || e)
  }
}

function recrawl() {
  const prompt = `请重新抓取 ${originKey.value}（${site.value?.title || ''}）的最新内容。`
  router.push('/chat')
  setTimeout(() => {
    if (chat.connected) {
      chat.send(prompt)
    } else {
      chat.lastDraft = prompt
    }
  }, 300)
}

function backToList() {
  router.push('/sites')
}

// 解码 cookie 字符串里的 URI 编码片段（仅做展示时更可读）
function decodeCookie(s) {
  if (!s) return ''
  try { return decodeURIComponent(s) } catch (_e) { return s }
}

onMounted(load)
watch(() => route.params.origin, load)
</script>

<template>
  <div class="page">
    <!-- 顶部：返回 + 标题 + 主操作 -->
    <div class="head">
      <button class="back" type="button" @click="backToList" aria-label="返回站点档案列表">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><polyline points="15 18 9 12 15 6" /></svg>
        返回站点档案
      </button>
      <h1 class="title" v-if="site">{{ site.title || site.origin }}</h1>
      <h1 class="title muted" v-else>站点档案</h1>
    </div>

    <div v-if="error" class="err-tip">{{ error }}</div>

    <div v-if="loading" class="muted">加载中…</div>

    <div v-else-if="site" class="content">
      <!-- 基本信息卡片 -->
      <section class="card">
        <header class="card-head">
          <h2>基本信息</h2>
          <div class="head-actions">
            <button v-if="!editing" class="btn btn-recrawl" type="button" @click="recrawl">一键复抓</button>
            <button v-if="!editing" class="btn btn-ghost" type="button" @click="startEdit">编辑</button>
          </div>
        </header>

        <dl class="kv">
          <dt>Origin</dt>
          <dd class="mono">{{ site.origin }}</dd>

          <dt>策略</dt>
          <dd>
            <span v-if="!editing" class="strategy">{{ strategyLabel(site.strategy) }}</span>
            <select v-else v-model="editForm.strategy">
              <option value="">未指定</option>
              <option v-for="o in strategyOptions" :key="o.value" :value="o.value">{{ o.label }}</option>
            </select>
          </dd>

          <dt>标题</dt>
          <dd>
            <span v-if="!editing">{{ site.title || '—' }}</span>
            <input v-else v-model="editForm.title" type="text" placeholder="站点标题" spellcheck="false" />
          </dd>

          <dt>备注</dt>
          <dd>
            <p v-if="!editing" class="notes">{{ site.notes || '—' }}</p>
            <textarea v-else v-model="editForm.notes" rows="6" placeholder="站点策略备注" spellcheck="false" />
          </dd>

          <dt>创建时间</dt>
          <dd class="mono faint">{{ fmtDate(site.created_at) }}</dd>

          <dt>上次抓取</dt>
          <dd class="mono faint">{{ fmtDate(site.last_crawled_at) }}</dd>
        </dl>

        <div v-if="editing" class="edit-actions">
          <button class="btn btn-recrawl" type="button" :disabled="saving" @click="save">
            <span v-if="saving" class="spin" />保存
          </button>
          <button class="btn btn-ghost" type="button" :disabled="saving" @click="cancelEdit">取消</button>
        </div>

        <div v-if="saveTip" class="result" :class="saveTip.startsWith('已') || saveTip.startsWith('Cookie') ? 'ok' : 'err'">
          {{ saveTip }}
        </div>
      </section>

      <!-- 凭据卡片 -->
      <section class="card">
        <header class="card-head">
          <h2>登录凭据</h2>
          <div class="head-actions">
            <button v-if="site.cookies && !editing" class="btn btn-ghost" type="button" @click="clearCookies">清除 Cookie</button>
          </div>
        </header>

        <div v-if="!editing">
          <div v-if="site.cookies" class="cookie-block">
            <p class="hint">已配置 Cookie · 点击右侧按钮可清除（清除后抓取需要重新登录）</p>
            <pre class="cookie-pre mono">{{ decodeCookie(site.cookies) }}</pre>
          </div>
          <p v-else class="muted">未配置 · 抓取需要登录的页面时请在编辑时粘贴 Cookie</p>
        </div>

        <div v-else>
          <p class="hint">留空则保持原值不变；输入新值覆盖；不填 Cookies 字段即提交 {{ '{ }' }} 表示清除。</p>
          <textarea
            v-model="editForm.cookies"
            rows="4"
            class="cookie-input mono"
            :placeholder="site.cookies ? '当前已配置 · 输入新值覆盖，留空表示不修改' : '粘贴 Cookie，如 wr_vid=xxx; wr_ssk=yyy'"
            spellcheck="false"
          />
        </div>
      </section>

      <!-- 自定义脚本卡片 -->
      <section v-if="site.script" class="card">
        <header class="card-head">
          <h2>自定义脚本</h2>
        </header>
        <p class="hint">AI 为该站点编写的 Python 脚本，下次抓取时会自动运行。</p>
        <pre class="script-pre mono">{{ site.script }}</pre>
      </section>

      <!-- 危险操作 -->
      <section class="card danger-zone">
        <header class="card-head">
          <h2>危险操作</h2>
        </header>
        <p class="muted">删除后该站点的策略备忘将被遗忘，之后再次抓取会重新分析。</p>
        <div class="edit-actions">
          <button class="btn btn-danger" type="button" @click="remove">删除档案</button>
        </div>
      </section>
    </div>
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
  padding: 24px 20px 40px;
  display: flex;
  flex-direction: column;
  gap: 18px;
}

.head {
  display: flex;
  flex-direction: column;
  gap: 8px;
  align-items: flex-start;
}
.back {
  appearance: none;
  background: transparent;
  border: 1px solid var(--line);
  color: var(--dim);
  border-radius: 9px;
  padding: 6px 12px;
  font-size: 12.5px;
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  transition: color .15s, border-color .15s;
}
.back:hover { color: var(--accent); border-color: var(--accent); }

.title {
  font-family: var(--font-display);
  font-size: 22px;
  font-weight: 700;
  margin: 0;
  word-break: break-word;
}
.title.muted { color: var(--dim); }

.muted { color: var(--dim); font-size: 13.5px; line-height: 1.6; }
.mono { font-family: var(--font-mono); }
.faint { color: var(--faint); }

.err-tip {
  padding: 10px 14px;
  background: var(--danger-soft);
  color: var(--danger);
  border: 1px solid color-mix(in srgb, var(--danger) 35%, transparent);
  border-radius: 8px;
  font-size: 13px;
}

.content {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.card {
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: var(--radius);
  box-shadow: var(--shadow);
  padding: 18px 20px;
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  flex-wrap: wrap;
}
.card-head h2 {
  margin: 0;
  font-size: 14px;
  font-weight: 600;
  color: var(--ink);
}
.head-actions { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }

.kv {
  display: grid;
  grid-template-columns: 110px 1fr;
  gap: 10px 18px;
  margin: 0;
  align-items: start;
}
.kv dt {
  font-size: 12px;
  color: var(--faint);
  letter-spacing: .04em;
  padding-top: 7px;
  text-transform: uppercase;
}
.kv dd {
  margin: 0;
  font-size: 14px;
  color: var(--ink);
  word-break: break-word;
  overflow-wrap: anywhere;
}
.kv dd.mono { font-family: var(--font-mono); font-size: 13px; }

input, textarea, select {
  appearance: none;
  border: 1px solid var(--line);
  background: var(--panel-2);
  color: var(--ink);
  font-family: var(--font-mono);
  font-size: 13px;
  padding: 8px 10px;
  border-radius: 9px;
  outline: none;
  transition: border-color .15s;
  width: 100%;
  min-width: 0;
  box-sizing: border-box;
}
input:focus, textarea:focus, select:focus { border-color: var(--accent); }
textarea { resize: vertical; min-height: 84px; line-height: 1.5; }
select { padding-right: 28px; background-image: linear-gradient(45deg, transparent 50%, var(--dim) 50%), linear-gradient(-45deg, transparent 50%, var(--dim) 50%); background-position: calc(100% - 14px) 50%, calc(100% - 9px) 50%; background-size: 5px 5px; background-repeat: no-repeat; }

.strategy {
  display: inline-block;
  padding: 3px 9px;
  border-radius: 999px;
  background: var(--accent-soft);
  color: var(--accent);
  font-size: 12px;
  font-family: var(--font-mono);
}
.notes {
  margin: 0;
  white-space: pre-wrap;
  font-size: 13.5px;
  color: var(--ink);
  line-height: 1.6;
}
.hint {
  font-size: 12px;
  color: var(--faint);
  margin: 0 0 8px;
}

.cookie-block {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.cookie-pre {
  background: var(--panel-2);
  border: 1px solid var(--line);
  border-radius: 9px;
  padding: 10px 12px;
  font-size: 12.5px;
  margin: 0;
  white-space: pre-wrap;
  word-break: break-all;
  max-height: 240px;
  overflow-y: auto;
  color: var(--dim);
}
.cookie-input { min-height: 100px; }

.script-pre {
  background: var(--panel-2);
  border: 1px solid var(--line);
  border-radius: 9px;
  padding: 12px 14px;
  font-size: 12px;
  line-height: 1.5;
  margin: 8px 0 0;
  white-space: pre-wrap;
  word-break: break-word;
  overflow-wrap: anywhere;
  max-height: 400px;
  overflow-y: auto;
  color: var(--dim);
}

.edit-actions {
  display: flex;
  gap: 10px;
  justify-content: flex-end;
  margin-top: 4px;
  flex-wrap: wrap;
}

.btn {
  appearance: none;
  border: 1px solid var(--line);
  border-radius: 9px;
  padding: 0 14px;
  height: 36px;
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  gap: 8px;
  transition: border-color .15s, background .15s, filter .15s, transform .1s;
}
.btn:disabled { opacity: .5; cursor: wait; }
.btn:active { transform: scale(.97); }
.btn-recrawl {
  background: var(--accent);
  color: var(--accent-ink);
  border-color: var(--accent);
}
.btn-recrawl:hover:not(:disabled) { filter: brightness(1.06); }
.btn-ghost { background: var(--panel-2); color: var(--dim); }
.btn-ghost:hover { color: var(--accent); border-color: var(--accent); }
.btn-danger {
  background: transparent;
  color: var(--danger);
  border-color: color-mix(in srgb, var(--danger) 50%, transparent);
}
.btn-danger:hover { background: var(--danger-soft); border-color: var(--danger); }

.result {
  font-size: 12.5px;
  padding: 8px 12px;
  border-radius: 8px;
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

.danger-zone .card-head h2 { color: var(--danger); }

.spin {
  width: 13px;
  height: 13px;
  border-radius: 50%;
  border: 2px solid currentColor;
  border-top-color: transparent;
  animation: rot .7s linear infinite;
}
@keyframes rot { to { transform: rotate(360deg); } }

@media (max-width: 640px) {
  .page { padding: 18px 14px 24px; }
  .kv { grid-template-columns: 1fr; gap: 4px 0; }
  .kv dt { padding-top: 4px; }
}
</style>
