<script setup>
// 爬行轨迹：一次工具调用 = 蛛丝上的一个节点，结果挂载在节点之下。
// 默认收起，只展示摘要；点击展开查看完整步骤。
import { computed, ref } from 'vue'

const props = defineProps({
  steps: { type: Array, required: true }, // [{ name, args, result, done }]
})

const collapsed = ref(true)

const RESULT_FOLD_AT = 220
const PREVIEW_AT = 120

const summary = computed(() => {
  const n = props.steps.length
  if (n === 0) return '调用 0 次'
  const done = props.steps.filter(s => s.done).length
  if (done < n) return `${n} 次调用 · ${done} 已完成`
  return `${n} 次工具调用`
})

function shortArgs(args) {
  if (!args) return ''
  return args.length > 110 ? args.slice(0, 110) + '…' : args
}
function isLong(result) {
  return result && result.length > RESULT_FOLD_AT
}
function preview(result) {
  return result.slice(0, PREVIEW_AT) + ' …'
}
</script>

<template>
  <div class="trace" :class="{ collapsed }">
    <button
      type="button"
      class="trace-head"
      :aria-expanded="!collapsed"
      @click="collapsed = !collapsed"
    >
      <span class="chevron" aria-hidden="true">{{ collapsed ? '▸' : '▾' }}</span>
      <span class="trace-label">爬行轨迹 · crawl trace</span>
      <span class="trace-summary">{{ summary }}</span>
    </button>

    <ol v-show="!collapsed" class="trace-body">
      <li v-for="(step, i) in steps" :key="i" class="step" :class="{ done: step.done }">
        <span class="step-name">{{ step.name }}()</span>
        <span class="step-args" :title="step.args">{{ shortArgs(step.args) }}</span>

        <details v-if="step.result && isLong(step.result)" class="step-result">
          <summary>{{ preview(step.result) }}</summary>
          <div>{{ step.result }}</div>
        </details>
        <div v-else-if="step.result" class="step-result">{{ step.result }}</div>
      </li>
    </ol>
  </div>
</template>

<style scoped>
.trace {
  align-self: flex-start;
  max-width: 92%;
  border: 1px solid var(--line);
  background: color-mix(in srgb, var(--panel) 70%, transparent);
  border-radius: var(--radius);
  font-family: var(--font-mono);
  font-size: 13px;
  animation: rise .35s var(--ease) both;
  overflow: hidden;
}

.trace-head {
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
.trace-head:hover { background: var(--accent-soft); }
.trace-head:focus-visible { outline-offset: -2px; }

.chevron {
  color: var(--accent);
  font-size: 11px;
  width: 10px;
  flex: none;
  transition: transform .2s var(--ease);
}

.trace-label {
  color: var(--faint);
  font-size: 11.5px;
  letter-spacing: .16em;
  text-transform: uppercase;
  flex: none;
}
.trace-summary {
  flex: 1;
  color: var(--dim);
  font-size: 12px;
  font-family: var(--font-mono);
  text-align: right;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.trace.collapsed .trace-head::after {
  content: "";
  flex: 0 0 0;
  border-bottom: 1px dashed var(--silk);
  margin-left: -4px;
  order: 99;
}

.trace-body {
  list-style: none;
  padding: 4px 16px 12px 14px;
  margin: 0;
  border-top: 1px dashed var(--silk);
}

.step {
  position: relative;
  padding: 4px 0 4px 22px;
  border-left: 1px dashed var(--silk);
  margin-left: 5px;
}
.step:last-child { border-left-color: transparent; }
.step::before {
  content: "";
  position: absolute;
  left: -5px;
  top: 12px;
  width: 9px;
  height: 9px;
  border-radius: 50%;
  background: var(--bg);
  border: 2px solid var(--accent);
}
.step.done::before { background: var(--accent); }
.step-name { color: var(--accent); font-weight: 600; }
.step-args {
  color: var(--faint);
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 100%;
}
.step-result {
  margin-top: 4px;
  border-left: 2px solid var(--line);
  padding: 4px 10px;
  color: var(--dim);
  font-size: 12.5px;
  white-space: pre-wrap;
  word-break: break-word;
}
details.step-result summary {
  cursor: pointer;
  color: var(--faint);
  list-style: none;
  min-height: 24px;
}
details.step-result summary::before { content: "▸ 结果 "; }
details.step-result[open] summary::before { content: "▾ 结果 "; }
details.step-result summary:hover { color: var(--accent); }

@media (max-width: 640px) {
  .trace { max-width: 100%; }
  .trace-summary { display: none; }
}
</style>
