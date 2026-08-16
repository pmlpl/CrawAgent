<script setup>
// 设置面板：API Key / Base URL / 模型；支持测试连接、密码可视化、保存。
import { ref, watch } from 'vue'
import { useSettings } from '../composables/useSettings'

const { state, save, test, close } = useSettings()
const showKey = ref(false)

// 关闭时清理瞬态
watch(() => state.open, (open) => {
  if (!open) {
    state.testResult = null
    state.saveTip = ''
    showKey.value = false
  }
})

function onBackdropClick() {
  close()
}
</script>

<template>
  <div v-if="state.open" class="backdrop" @click="onBackdropClick" />
  <div v-if="state.open" class="modal" role="dialog" aria-modal="true" aria-label="设置">
    <section class="card" @click.stop>
      <header class="head">
        <h2>设置</h2>
        <button type="button" class="close" aria-label="关闭" @click="close">×</button>
      </header>

      <p v-if="state.loading" class="muted">加载中…</p>

      <form v-else class="form" @submit.prevent="save">
        <label class="field">
          <span class="label">
            API Key
            <i v-if="state.apiKeySet" class="badge">已配置</i>
          </span>
          <div class="input-row">
            <input
              :type="showKey ? 'text' : 'password'"
              v-model="state.apiKey"
              :placeholder="state.apiKeySet ? '已配置 · 留空表示不修改' : 'sk-...'"
              autocomplete="off"
              spellcheck="false"
            />
            <button
              type="button"
              class="ghost"
              :aria-label="showKey ? '隐藏' : '显示'"
              @click="showKey = !showKey"
            >
              <svg v-if="!showKey" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8S1 12 1 12z" /><circle cx="12" cy="12" r="3" /></svg>
              <svg v-else width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24" /><line x1="1" y1="1" x2="23" y2="23" /></svg>
            </button>
          </div>
          <span class="hint">支持 OpenAI 兼容接口（DeepSeek / 通义千问 / Moonshot 等）</span>
        </label>

        <label class="field">
          <span class="label">Base URL</span>
          <input
            type="text"
            v-model="state.baseUrl"
            placeholder="https://api.deepseek.com/v1"
            autocomplete="off"
            spellcheck="false"
          />
        </label>

        <label class="field">
          <span class="label">模型</span>
          <input
            type="text"
            v-model="state.model"
            placeholder="deepseek-chat"
            autocomplete="off"
            spellcheck="false"
          />
        </label>

        <div v-if="state.testResult" class="result" :class="state.testResult.ok ? 'ok' : 'err'">
          {{ state.testResult.msg }}
        </div>
        <div v-if="state.saveTip" class="result" :class="state.saveTip.startsWith('已') ? 'ok' : 'err'">
          {{ state.saveTip }}
        </div>

        <div class="actions">
          <button type="button" class="btn-ghost" :disabled="state.testing" @click="test">
            <span v-if="state.testing" class="spin" />测试连接
          </button>
          <button type="submit" class="btn-primary" :disabled="state.saving">
            <span v-if="state.saving" class="spin" />保存
          </button>
        </div>
      </form>
    </section>
  </div>
</template>

<style scoped>
.backdrop {
  position: fixed;
  inset: 0;
  z-index: 50;
  background: rgba(0, 0, 0, .5);
  animation: fade .2s var(--ease) both;
}

.modal {
  position: fixed;
  inset: 0;
  z-index: 51;
  display: grid;
  place-items: center;
  padding: 20px;
  pointer-events: none;
}

.card {
  pointer-events: auto;
  width: min(460px, 100%);
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 16px;
  box-shadow: var(--shadow);
  padding: 22px 22px 20px;
  animation: rise .25s var(--ease) both;
}

.head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 18px;
}
.head h2 {
  font-size: 17px;
  font-weight: 600;
  color: var(--ink);
}
.close {
  appearance: none;
  border: none;
  background: transparent;
  color: var(--dim);
  font-size: 24px;
  line-height: 1;
  cursor: pointer;
  width: 32px;
  height: 32px;
  border-radius: 8px;
  display: grid;
  place-items: center;
  transition: background .15s, color .15s;
}
.close:hover { background: var(--accent-soft); color: var(--accent); }

.muted { color: var(--faint); font-size: 13.5px; padding: 30px 0; text-align: center; }

.form {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.field {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.label {
  font-size: 12.5px;
  font-weight: 500;
  color: var(--dim);
  display: flex;
  align-items: center;
  gap: 8px;
}

.badge {
  font-size: 10.5px;
  font-weight: 500;
  font-family: var(--font-mono);
  padding: 2px 7px;
  border-radius: 999px;
  background: var(--accent-soft);
  color: var(--accent);
  letter-spacing: .04em;
}

.input-row {
  display: flex;
  gap: 6px;
  align-items: stretch;
}
.input-row input { flex: 1; }

input {
  appearance: none;
  border: 1px solid var(--line);
  background: var(--panel-2);
  color: var(--ink);
  font-family: var(--font-mono);
  font-size: 13.5px;
  padding: 9px 12px;
  border-radius: 9px;
  outline: none;
  transition: border-color .15s;
  min-width: 0;
}
input:focus { border-color: var(--accent); }
input::placeholder { color: var(--faint); font-family: var(--font-body); }

.ghost {
  appearance: none;
  border: 1px solid var(--line);
  background: var(--panel-2);
  color: var(--dim);
  width: 38px;
  border-radius: 9px;
  cursor: pointer;
  display: grid;
  place-items: center;
  transition: color .15s, border-color .15s;
}
.ghost:hover { color: var(--accent); border-color: var(--accent); }

.hint {
  font-size: 12px;
  color: var(--faint);
  margin-top: 2px;
}

.result {
  font-size: 12.5px;
  padding: 8px 12px;
  border-radius: 8px;
  word-break: break-word;
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

.actions {
  display: flex;
  gap: 10px;
  justify-content: flex-end;
  margin-top: 6px;
}

.btn-ghost, .btn-primary {
  appearance: none;
  border: 1px solid var(--line);
  border-radius: 9px;
  padding: 0 18px;
  height: 40px;
  font-size: 13.5px;
  font-weight: 500;
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  gap: 8px;
  transition: border-color .15s, background .15s, transform .1s;
}
.btn-ghost {
  background: var(--panel-2);
  color: var(--dim);
}
.btn-ghost:hover { color: var(--accent); border-color: var(--accent); }
.btn-primary {
  background: var(--accent);
  color: var(--accent-ink);
  border-color: var(--accent);
}
.btn-primary:hover { filter: brightness(1.06); }
.btn-primary:active, .btn-ghost:active { transform: scale(.97); }
.btn-ghost:disabled, .btn-primary:disabled { opacity: .5; cursor: wait; }

.spin {
  width: 13px;
  height: 13px;
  border-radius: 50%;
  border: 2px solid currentColor;
  border-top-color: transparent;
  animation: rot .7s linear infinite;
}

@keyframes fade {
  from { opacity: 0; }
  to { opacity: 1; }
}

@media (max-width: 480px) {
  .card { padding: 18px 16px; }
  .actions { flex-direction: column-reverse; }
  .btn-ghost, .btn-primary { width: 100%; justify-content: center; }
}
</style>
