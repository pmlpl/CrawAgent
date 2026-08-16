<script setup>
// AI 思考内容（推理模型的 reasoning_content）—— 默认收起，展开查看完整推理过程。
import { computed, ref } from 'vue'

const props = defineProps({
  content: { type: String, required: true },
})

const collapsed = ref(true)

const summary = computed(() => {
  const len = props.content.length
  return len > 1000 ? `${(len / 1000).toFixed(1)}k 字` : `${len} 字`
})
</script>

<template>
  <div class="thinking" :class="{ collapsed }">
    <button
      type="button"
      class="think-head"
      :aria-expanded="!collapsed"
      @click="collapsed = !collapsed"
    >
      <span class="chevron" aria-hidden="true">{{ collapsed ? '▸' : '▾' }}</span>
      <span class="think-icon" aria-hidden="true">
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9.5 2A2.5 2.5 0 0 1 12 4.5v15a2.5 2.5 0 0 1-4.96.44 2.5 2.5 0 0 1-2.96-3.08 3 3 0 0 1-.34-5.58 2.5 2.5 0 0 1 1.32-4.24 2.5 2.5 0 0 1 1.98-3A2.5 2.5 0 0 1 9.5 2Z" /><path d="M14.5 2A2.5 2.5 0 0 0 12 4.5v15a2.5 2.5 0 0 0 4.96.44 2.5 2.5 0 0 0 2.96-3.08 3 3 0 0 0 .34-5.58 2.5 2.5 0 0 0-1.32-4.24 2.5 2.5 0 0 0-1.98-3A2.5 2.5 0 0 0 14.5 2Z" /></svg>
      </span>
      <span class="think-label">思考过程 · thinking</span>
      <span class="think-summary">{{ summary }}</span>
    </button>

    <div v-show="!collapsed" class="think-body">
      <pre>{{ content }}</pre>
    </div>
  </div>
</template>

<style scoped>
.thinking {
  align-self: flex-start;
  max-width: 92%;
  border: 1px dashed color-mix(in srgb, var(--accent) 35%, transparent);
  background: color-mix(in srgb, var(--accent-soft) 50%, transparent);
  border-radius: var(--radius);
  font-family: var(--font-mono);
  font-size: 13px;
  animation: rise .35s var(--ease) both;
  overflow: hidden;
}

.think-head {
  width: 100%;
  appearance: none;
  border: none;
  background: transparent;
  color: inherit;
  font: inherit;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 9px 14px 9px 12px;
  cursor: pointer;
  text-align: left;
  transition: background .15s;
}
.think-head:hover { background: color-mix(in srgb, var(--accent-soft) 70%, transparent); }
.think-head:focus-visible { outline-offset: -2px; }

.chevron {
  color: var(--accent);
  font-size: 11px;
  width: 10px;
  flex: none;
}

.think-icon {
  color: var(--accent);
  display: grid;
  place-items: center;
  flex: none;
}

.think-label {
  color: var(--accent);
  font-size: 11.5px;
  letter-spacing: .14em;
  text-transform: uppercase;
  flex: none;
  opacity: .85;
}
.think-summary {
  flex: 1;
  color: var(--dim);
  font-size: 12px;
  font-family: var(--font-mono);
  text-align: right;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.think-body {
  border-top: 1px dashed color-mix(in srgb, var(--accent) 28%, transparent);
  padding: 12px 14px;
  max-height: 360px;
  overflow-y: auto;
}
.think-body pre {
  margin: 0;
  font-family: var(--font-mono);
  font-size: 12.5px;
  color: var(--dim);
  white-space: pre-wrap;
  word-break: break-word;
  line-height: 1.65;
}

.think-body::-webkit-scrollbar { width: 6px; }
.think-body::-webkit-scrollbar-thumb {
  background: color-mix(in srgb, var(--accent) 30%, transparent);
  border-radius: 999px;
}

@media (max-width: 640px) {
  .thinking { max-width: 100%; }
  .think-summary { display: none; }
}
</style>
