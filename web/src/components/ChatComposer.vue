<script setup>
// 输入区 v2：现代化 AI 对话输入栏
// 设计参考：大号圆角输入框 + 内嵌工具栏 + 性能指标状态栏
// - 工具栏：附件、Agent 选择、模型&推理强度、思考开关、发送
// - 状态栏：复用后端 metrics.status_line() 输出
import { computed, nextTick, ref, watch } from 'vue'
import { storeToRefs } from 'pinia'
import { useChat } from '../composables/useChat'
import { useSettings } from '../composables/useSettings'

const props = defineProps({
  busy: { type: Boolean, default: false },
  restore: { type: String, default: '' },
})
const emit = defineEmits(['send', 'stop'])

const chat = useChat()
const settings = useSettings()

// 直接绑定 useChat 的 draft（按会话独立保存到 localStorage）
// draft 是可写 computed：storeToRefs 保 ref 形态，v-model 与 .value 都能用
const { draft } = storeToRefs(chat)
const inputEl = ref(null)
const toolbarRef = ref(null)

// ---------- 模型 & 推理强度显示 ----------
const MODEL_META = {
  'deepseek-v4-flash': { label: 'V4 FLASH', desc: '快速对话（默认）' },
  'deepseek-v4-flash-vision-exp': { label: 'V4 FLASH VISION', desc: '视觉快速版' },
  'deepseek-v4': { label: 'V4', desc: '标准版' },
  'deepseek-v4-pro': { label: 'V4 PRO', desc: '深度推理（慢）' },
  'deepseek-chat': { label: 'V3 CHAT', desc: '稳定版' },
}

function fmtModelId(id) {
  if (!id) return ''
  return id
    .replace(/^(deepseek|gpt|claude|gemini|kimi|qwen|glm|minimax)[-_]*/i, '')
    .replace(/[-_]/g, ' ')
    .trim()
    .toUpperCase() || id
}

const availableModels = computed(() => {
  // state.models 现在是 [{name, provider}]；label 优先展示模型名，desc 显示服务商
  const configured = Array.isArray(settings.state.models) ? settings.state.models : []
  const list = configured.length
    ? configured.map(m => m.name)
    : [settings.state.model || settings.state.defaultModel || 'deepseek-v4-flash']
  return list.map(value => {
    const entry = configured.find(m => m.name === value)
    const meta = MODEL_META[value] || {}
    return { value, label: meta.label || fmtModelId(value), desc: entry?.provider || value }
  })
})

const hasManyModels = computed(() => availableModels.value.length > 1)

const modelLabel = computed(() => {
  const m = settings.state.model || settings.state.defaultModel || availableModels.value[0]?.value || ''
  const found = availableModels.value.find(x => x.value === m)
  return found ? found.label : fmtModelId(m)
})

const thinkingLabel = computed(() => {
  const d = settings.state.thinkingDepth || 'high'
  const map = { off: 'Off', low: 'Low', high: 'High', max: 'Max', medium: 'High' }
  return map[d] || 'High'
})

// 思考开关动画（busy 时旋转）
const thinkingSpin = computed(() => props.busy)

// ---------- 下拉菜单 ----------
const showModelMenu = ref(false)
const showThinkMenu = ref(false)

// 点击外部关闭
function onDocClick(ev) {
  if (!toolbarRef.value) return
  if (!toolbarRef.value.contains(ev.target)) {
    showModelMenu.value = false
    showThinkMenu.value = false
  }
}
watch([showModelMenu, showThinkMenu], () => {
  if (showModelMenu.value || showThinkMenu.value) {
    document.addEventListener('click', onDocClick)
  } else {
    document.removeEventListener('click', onDocClick)
  }
})

// ---------- 输入逻辑 ----------
function autoGrow() {
  const el = inputEl.value
  if (!el) return
  el.style.height = 'auto'
  el.style.height = Math.min(el.scrollHeight, 200) + 'px'
}

let composing = false
function onKeydown(ev) {
  if (ev.key === 'Enter' && !ev.shiftKey && !composing) {
    ev.preventDefault()
    submit()
  }
}

function submit() {
  const text = draft.value.trim()
  if (!text || props.busy) return
  if (emit('send', text) !== false) {
    draft.value = ''
    nextTick(autoGrow)
  }
}

watch(() => props.restore, (val) => {
  if (val && !draft.value) {
    draft.value = val
    nextTick(autoGrow)
  }
})

function fill(text) {
  draft.value = text
  nextTick(() => { autoGrow(); inputEl.value?.focus() })
}

// ---------- 思考深度切换 ----------
const thinkOptions = [
  { value: 'off', label: 'Off', desc: '关闭思考' },
  { value: 'low', label: 'Low', desc: '快速推理' },
  { value: 'high', label: 'High', desc: '深度推理' },
  { value: 'max', label: 'Max', desc: '最深度推理' },
]

function setThinkDepth(v) {
  settings.state.thinkingDepth = v
  showThinkMenu.value = false
  settings.saveThinking()
}

function setModel(v) {
  settings.setSelectedModel(v)
  showModelMenu.value = false
}

function toggleThinkingMode() {
  const current = settings.state.thinkingDepth
  settings.state.thinkingDepth = current === 'off' ? 'high' : 'off'
  settings.saveThinking()
}

defineExpose({ fill })
</script>

<template>
  <footer class="composer-wrap">
    <!-- 主输入区 -->
    <form class="composer" @submit.prevent="submit" :class="{ focused: false }">
      <textarea
        ref="inputEl"
        v-model="draft"
        rows="1"
        placeholder="给智能体发消息"
        aria-label="任务输入"
        @input="autoGrow"
        @keydown="onKeydown"
        @compositionstart="composing = true"
        @compositionend="composing = false"
      />

      <!-- 内嵌工具栏 -->
      <div class="toolbar" ref="toolbarRef">
        <div class="tool-group left">
          <!-- 附件 -->
          <button class="tool-btn" type="button" title="添加附件" aria-label="添加附件">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="5" x2="12" y2="19" /><line x1="5" y1="12" x2="19" y2="12" /></svg>
          </button>
        </div>

        <div class="tool-group right">
          <!-- 模型选择：只显示设置里配置的模型 -->
          <div class="select-wrap" @click.stop>
            <button
              class="tool-btn select"
              type="button"
              :disabled="!hasManyModels"
              @click="hasManyModels && (showModelMenu = !showModelMenu)"
            >
              <span class="model-name">{{ modelLabel }}</span>
              <svg v-if="hasManyModels" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><polyline points="6 9 12 15 18 9" /></svg>
            </button>
            <div v-if="showModelMenu && hasManyModels" class="dropdown-menu">
              <button
                v-for="m in availableModels"
                :key="m.value"
                type="button"
                class="dropdown-item"
                :class="{ active: settings.state.model === m.value }"
                @click="setModel(m.value)"
              >
                <span class="di-name">{{ m.label }}</span>
                <span class="di-desc">{{ m.desc }}</span>
              </button>
            </div>
          </div>

          <!-- 推理强度 -->
          <div class="select-wrap" @click.stop>
            <button
              class="tool-btn select think"
              type="button"
              @click="showThinkMenu = !showThinkMenu"
            >
              <span class="think-label">{{ thinkingLabel }}</span>
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><polyline points="6 9 12 15 18 9" /></svg>
            </button>
            <div v-if="showThinkMenu" class="dropdown-menu">
              <button
                v-for="t in thinkOptions"
                :key="t.value"
                type="button"
                class="dropdown-item"
                :class="{ active: settings.state.thinkingDepth === t.value }"
                @click="setThinkDepth(t.value)"
              >
                <span class="di-name">{{ t.label }}</span>
                <span class="di-desc">{{ t.desc }}</span>
              </button>
            </div>
          </div>

          <!-- 思考开关（动画） -->
          <div class="think-toggle" :class="{ active: settings.state.thinkingDepth !== 'off', spinning: thinkingSpin }" title="思考模式 · 点击切换" @click="toggleThinkingMode">
            <div class="orbit" />
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
              <path d="M9.5 2A2.5 2.5 0 0 1 12 4.5v15a2.5 2.5 0 0 1-4.96.44 2.5 2.5 0 0 1-2.96-3.08 3 3 0 0 1-.34-5.58 2.5 2.5 0 0 1 1.32-4.24 2.5 2.5 0 0 1 1.98-3A2.5 2.5 0 0 1 9.5 2Z" />
              <path d="M14.5 2A2.5 2.5 0 0 0 12 4.5v15a2.5 2.5 0 0 0 4.96.44 2.5 2.5 0 0 0 2.96-3.08 3 3 0 0 0 .34-5.58 2.5 2.5 0 0 0-1.32-4.24 2.5 2.5 0 0 0-1.98-3A2.5 2.5 0 0 0 14.5 2Z" />
            </svg>
          </div>

          <!-- 发送 / 停止 -->
          <button v-if="!busy" class="send-btn" type="submit" aria-label="发送">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><line x1="12" y1="19" x2="12" y2="5" /><polyline points="5 12 12 5 19 12" /></svg>
          </button>
          <button v-else class="stop-btn" type="button" @click="emit('stop')" aria-label="停止">
            <span class="stop-square" />
          </button>
        </div>
      </div>
    </form>

    <!-- 状态栏 -->
    <div v-if="chat.lastStatus" class="status-bar">
      <span class="status-text">{{ chat.lastStatus }}</span>
    </div>
  </footer>
</template>

<style scoped>
.composer-wrap {
  flex: none;
  z-index: 10;
  padding: 8px 16px calc(12px + env(safe-area-inset-bottom));
  display: flex;
  flex-direction: column;
  gap: 6px;
  background: linear-gradient(to top, var(--bg) 60%, transparent);
}

/* 主输入框 */
.composer {
  max-width: var(--maxw);
  margin: 0 auto;
  width: 100%;
  background: var(--panel);
  border: 1.5px solid var(--line);
  border-radius: 28px;
  padding: 4px 4px 4px 20px;
  box-shadow: 0 4px 24px color-mix(in srgb, var(--ink) 4%, transparent), 0 1px 3px color-mix(in srgb, var(--ink) 6%, transparent);
  transition: border-color .2s, box-shadow .2s;
  display: flex;
  flex-direction: column;
  gap: 0;
}
.composer:focus-within {
  border-color: var(--accent);
  box-shadow: 0 4px 32px color-mix(in srgb, var(--accent) 10%, transparent), 0 1px 3px color-mix(in srgb, var(--ink) 6%, transparent);
}

textarea {
  width: 100%;
  appearance: none;
  border: none;
  background: transparent;
  color: var(--ink);
  font-family: var(--font-body);
  font-size: 16px;
  line-height: 1.6;
  resize: none;
  max-height: 200px;
  padding: 12px 0 4px;
  outline: none;
}
textarea::placeholder {
  color: var(--faint);
  font-size: 15px;
}

/* 工具栏 */
.toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 4px 6px 6px;
  gap: 6px;
}
.tool-group {
  display: flex;
  align-items: center;
  gap: 4px;
}

/* 工具按钮 */
.tool-btn {
  appearance: none;
  border: none;
  background: transparent;
  color: var(--dim);
  height: 32px;
  padding: 0 10px;
  border-radius: 10px;
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  font-family: var(--font-mono);
  transition: background .15s, color .15s;
  white-space: nowrap;
}
.tool-btn:hover { background: var(--panel-2); color: var(--ink); }
.tool-btn:disabled { opacity: .6; cursor: default; }
.tool-btn.select {
  font-family: var(--font-mono);
  font-size: 12.5px;
}
.tool-btn.select .model-name,
.tool-btn.select .think-label {
  font-weight: 600;
}
.tool-btn.select .model-name {
  color: var(--ink);
  letter-spacing: .02em;
}
.tool-btn.select .think-label {
  color: var(--accent);
}

/* 下拉菜单 */
.select-wrap { position: relative; }
.dropdown-menu {
  position: absolute;
  bottom: calc(100% + 6px);
  right: 0;
  min-width: 180px;
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 14px;
  padding: 6px;
  box-shadow: 0 8px 32px color-mix(in srgb, var(--ink) 10%, transparent);
  display: flex;
  flex-direction: column;
  gap: 2px;
  z-index: 100;
}
.dropdown-item {
  appearance: none;
  border: none;
  background: transparent;
  color: var(--ink);
  padding: 8px 12px;
  border-radius: 10px;
  cursor: pointer;
  display: flex;
  flex-direction: column;
  gap: 2px;
  text-align: left;
  transition: background .12s;
}
.dropdown-item:hover { background: var(--panel-2); }
.dropdown-item.active { background: var(--accent-soft); }
.di-name { font-size: 13px; font-weight: 600; }
.di-desc { font-size: 11.5px; color: var(--faint); }

/* 思考开关 */
.think-toggle {
  width: 32px;
  height: 32px;
  border-radius: 10px;
  display: grid;
  place-items: center;
  color: var(--faint);
  cursor: pointer;
  position: relative;
  transition: color .2s;
}
.think-toggle.active { color: var(--accent); }
.think-toggle.spinning svg { animation: think-pulse 1.4s ease-in-out infinite; }
.think-toggle .orbit {
  position: absolute;
  inset: 1px;
  border-radius: 50%;
  border: 1.5px solid transparent;
  border-top-color: var(--accent);
  animation: think-spin 1.2s linear infinite;
  opacity: 0;
}
.think-toggle.spinning .orbit { opacity: 1; }

@keyframes think-spin {
  to { transform: rotate(360deg); }
}
@keyframes think-pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: .5; }
}

/* 发送按钮 */
.send-btn {
  width: 36px;
  height: 36px;
  border-radius: 10px;
  border: none;
  background: var(--accent);
  color: var(--accent-ink);
  cursor: pointer;
  display: grid;
  place-items: center;
  transition: transform .12s, filter .2s;
}
.send-btn:hover { filter: brightness(1.08); }
.send-btn:active { transform: scale(.94); }

/* 停止按钮 */
.stop-btn {
  width: 36px;
  height: 36px;
  border-radius: 10px;
  border: none;
  background: #e5484d;
  color: #fff;
  cursor: pointer;
  display: grid;
  place-items: center;
  transition: transform .12s, filter .2s;
}
.stop-btn:hover { filter: brightness(1.1); }
.stop-btn:active { transform: scale(.94); }
.stop-square {
  width: 12px;
  height: 12px;
  background: currentColor;
  border-radius: 3px;
}

/* 状态栏 */
.status-bar {
  max-width: var(--maxw);
  margin: 0 auto;
  width: 100%;
  padding: 0 8px;
  font-family: var(--font-mono);
  font-size: 11.5px;
  color: var(--faint);
  letter-spacing: .01em;
  overflow: hidden;
  white-space: nowrap;
}
.status-text {
  display: inline-block;
  overflow: hidden;
  text-overflow: ellipsis;
  width: 100%;
}

@media (max-width: 640px) {
  .composer-wrap { padding: 6px 10px calc(10px + env(safe-area-inset-bottom)); }
  .composer { border-radius: 22px; padding: 4px 4px 4px 14px; }
  textarea { font-size: 15px; }
  .toolbar { flex-wrap: wrap; gap: 4px; }
  .tool-group.right { flex-wrap: wrap; }
}
</style>
