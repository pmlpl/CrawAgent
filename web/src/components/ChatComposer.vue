<script setup>
// 输入区：自动增高、Enter 发送 / Shift+Enter 换行、中文输入法 composition 保护。
// busy 时按钮变为 spinner；提交后把文本 emit 给父级，由父级决定是否清空。
import { nextTick, ref, watch } from 'vue'

const props = defineProps({
  busy: { type: Boolean, default: false },
  restore: { type: String, default: '' }, // 出错时需回填的草稿
})
const emit = defineEmits(['send'])

const draft = ref('')
const inputEl = ref(null)

function autoGrow() {
  const el = inputEl.value
  if (!el) return
  el.style.height = 'auto'
  el.style.height = Math.min(el.scrollHeight, 180) + 'px'
}

// 输入法组合期间 Enter 不触发发送
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

// 父级要求恢复草稿（发送失败时）
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

defineExpose({ fill })
</script>

<template>
  <footer class="composer-wrap">
    <form class="composer" @submit.prevent="submit">
      <textarea
        ref="inputEl"
        v-model="draft"
        rows="1"
        placeholder="丢给 Spider 一个 URL 或任务…"
        aria-label="任务输入"
        @input="autoGrow"
        @keydown="onKeydown"
        @compositionstart="composing = true"
        @compositionend="composing = false"
      />
      <button class="send" type="submit" :disabled="busy" aria-label="发送">
        <svg v-if="!busy" width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M22 2 11 13" /><path d="M22 2 15 22l-4-9-9-4z" /></svg>
        <span v-else class="spin" />
      </button>
    </form>
  </footer>
</template>

<style scoped>
.composer-wrap {
  flex: none;
  z-index: 10;
  padding: 12px 20px calc(14px + env(safe-area-inset-bottom));
  background: linear-gradient(to top, var(--bg) 55%, transparent);
}
.composer {
  max-width: var(--maxw);
  margin: 0 auto;
  display: flex;
  align-items: flex-end;
  gap: 10px;
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 20px;
  padding: 8px 8px 8px 18px;
  box-shadow: var(--shadow);
  transition: border-color .2s;
}
.composer:focus-within { border-color: var(--accent); }
textarea {
  flex: 1;
  appearance: none;
  border: none;
  background: transparent;
  color: var(--ink);
  font-family: var(--font-body);
  font-size: 16px;
  line-height: 1.55;
  resize: none;
  max-height: 180px;
  padding: 8px 0;
  outline: none;
}
textarea::placeholder { color: var(--faint); }
.send {
  flex: none;
  width: 46px;
  height: 46px;
  border-radius: 14px;
  border: none;
  background: var(--accent);
  color: var(--accent-ink);
  cursor: pointer;
  display: grid;
  place-items: center;
  transition: transform .12s, filter .2s;
}
.send:hover { filter: brightness(1.08); }
.send:active { transform: scale(.94); }
.send:disabled { cursor: wait; filter: saturate(.4) brightness(.85); }
.spin {
  width: 18px;
  height: 18px;
  border-radius: 50%;
  border: 2px solid var(--accent-ink);
  border-top-color: transparent;
  animation: rot .7s linear infinite;
}
@media (max-width: 640px) {
  .composer-wrap { padding: 10px 12px calc(12px + env(safe-area-inset-bottom)); }
}
</style>
