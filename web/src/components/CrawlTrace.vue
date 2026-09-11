<script setup>
// 爬行轨迹：一次工具调用 = 蛛丝上的一个节点，结果挂载在节点之下。
// 默认展开，展示完整步骤；点击可收起。
import { computed, nextTick, ref, watch } from 'vue'

const props = defineProps({
  steps: { type: Array, required: true }, // [{ name, args, result, done }]
  expanded: { type: Boolean, default: false },
  roundId: { type: Number, default: 0 },
})

const collapsed = ref(!props.expanded)
const progressRefs = ref({}) // step index → DOM element（用于自动滚动）

watch(() => props.expanded, (val) => {
  collapsed.value = !val
})


// 进度日志新增行时自动滚到底部
watch(
  () => props.steps.map(s => s.progress_lines?.length || 0).join(','),
  () => {
    nextTick(() => {
      for (const el of Object.values(progressRefs.value)) {
        if (el) el.scrollTop = el.scrollHeight
      }
    })
  }
)

const summary = computed(() => {
  const n = props.steps.length
  if (n === 0) return '等待 AI 生成计划…'
  const thinkCount = props.steps.filter(s => kindOf(s) === 'thinking').length
  const toolCount = n - thinkCount
  const done = props.steps.filter(s => s.done).length
  const parts = []
  if (thinkCount) parts.push(`${thinkCount} 思考`)
  if (toolCount) parts.push(`${toolCount} 工具调用`)
  if (done < n) parts.push(`${done}/${n} 已完成`)
  else parts.push('全部完成')
  return parts.join(' · ')
})

function kindOf(step) {
  return step && step.kind === 'thinking' ? 'thinking' : 'tool'
}

function thinkSummary(text) {
  if (!text) return ''
  const flat = text.replace(/\s+/g, ' ').trim()
  // 折叠 summary 最多显示 50 字截断，展开处看完整内容，避免 summary 和详情显示同一句话
  if (flat.length <= 50) return flat
  return flat.slice(0, 50) + '…'
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
      <span class="trace-label">爬行轨迹 · crawl trace<span v-if="roundId > 0" class="trace-round">#{{ roundId }}</span></span>
      <span class="trace-summary">{{ summary }}</span>
    </button>

    <ol v-show="!collapsed" class="trace-body">
      <li v-for="(step, i) in steps" :key="i" class="step" :class="[`step-${kindOf(step)}`, { done: step.done }]">

        <!-- THINKING STEP：AI 的思考/工具调用计划 -->
        <template v-if="kindOf(step) === 'thinking'">
          <div class="step-bullet think-bullet" aria-hidden="true" title="思考">
            <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><path d="M9.5 2A2.5 2.5 0 0 1 12 4.5v15a2.5 2.5 0 0 1-4.96.44 2.5 2.5 0 0 1-2.96-3.08 3 3 0 0 1-.34-5.58 2.5 2.5 0 0 1 1.32-4.24 2.5 2.5 0 0 1 1.98-3A2.5 2.5 0 0 1 9.5 2Z" /></svg>
          </div>
          <span class="step-kind think-kind">thinking · 思考</span>
          <details class="step-thinking" :open="step.streaming || step._opened" @toggle="step._opened = $event.target.open">
            <summary>
              <span class="think-summary-text">{{ thinkSummary(step.content) }}</span>
              <span v-if="step.streaming" class="think-live" aria-hidden="true">流式中…</span>
            </summary>
            <pre class="thinking-pre" :class="{ streaming: step.streaming }">{{ step.content }}</pre>
          </details>
        </template>

        <!-- TOOL CALL STEP -->
        <template v-else>
          <span class="step-name">{{ step.name }}()</span>
          <span v-if="!step.done" class="step-running">
            <span class="run-dot" /><span class="run-text">运行中…</span>
          </span>
          <span class="step-args">{{ step.args }}</span>
          <!-- 子 Agent 内联进度日志（video_site_expert 等长耗时工具） -->
          <div
            v-if="step.progress_lines && step.progress_lines.length"
            class="step-progress"
            :ref="el => { if (el) progressRefs[i] = el }"
          >
            <div v-for="(ln, li) in step.progress_lines" :key="li" class="progress-line" :class="{ latest: li === step.progress_lines.length - 1 && !step.done }">
              <span class="progress-bullet">•</span>
              <span class="progress-text">{{ ln }}</span>
            </div>
          </div>
          <div v-if="step.media && step.media.length" class="step-media">
            <template v-for="(m, mi) in step.media" :key="m.url || mi">
              <video v-if="m.type === 'video'" :src="m.url" controls preload="metadata" class="step-video" />
              <img v-else-if="m.type === 'image'" :src="m.url" :alt="m.filename" class="step-img" />
              <audio v-else-if="m.type === 'audio'" :src="m.url" controls preload="metadata" class="step-audio" />
            </template>
          </div>
          <div v-if="step.result" class="step-result-wrap">
            <details class="step-result" :open="step.result.length <= 2000 || (step.media && step.media.length > 0)">
              <summary v-if="step.result.length > 2000">结果（{{ step.result.length }} 字符，点击展开）</summary>
              <div class="step-result-content">{{ step.result }}</div>
            </details>
          </div>
        </template>

      </li>
    </ol>
  </div>
</template>

<style scoped>
.trace {
  align-self: flex-start;
  max-width: 92%;
  border: 1px solid var(--line);
  border-left: 3px solid var(--accent);
  background: color-mix(in srgb, var(--panel) 70%, transparent);
  border-radius: var(--radius);
  font-family: var(--font-mono);
  font-size: 13px;
  animation: rise .35s var(--ease) both;
  overflow: visible;
  position: relative;
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
.trace-round {
  margin-left: 6px;
  padding: 1px 6px;
  font-size: 10px;
  background: color-mix(in srgb, var(--accent) 20%, transparent);
  border: 1px solid color-mix(in srgb, var(--accent) 40%, transparent);
  border-radius: 4px;
  color: var(--accent);
  letter-spacing: 0;
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
  max-height: 60vh;
  overflow-y: auto;
  overflow-x: hidden;
  scrollbar-width: thin;
  scrollbar-color: var(--accent) transparent;
}
.trace-body::-webkit-scrollbar {
  width: 6px;
}
.trace-body::-webkit-scrollbar-track {
  background: transparent;
}
.trace-body::-webkit-scrollbar-thumb {
  background: var(--accent);
  border-radius: 3px;
  opacity: 0.5;
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

/* ---- THINKING STEP 样式（发光脑图点 + 虚线框） ---- */
.step.step-thinking {
  padding-left: 28px;
  margin-left: 2px;
}
.step.step-thinking::before {
  display: none; /* 用 think-bullet 代替 */
}
.think-bullet {
  position: absolute;
  left: -6px;
  top: 4px;
  width: 18px;
  height: 18px;
  border-radius: 50%;
  background: color-mix(in srgb, var(--accent) 18%, transparent);
  border: 1px dashed var(--accent);
  color: var(--accent);
  display: grid;
  place-items: center;
  box-shadow: 0 0 0 2px color-mix(in srgb, var(--accent) 8%, transparent);
}
.think-kind {
  display: inline-block;
  margin-right: 8px;
  padding: 1px 7px;
  font-size: 10.5px;
  letter-spacing: .14em;
  text-transform: uppercase;
  border-radius: 4px;
  border: 1px dashed var(--accent);
  color: var(--accent);
  background: color-mix(in srgb, var(--accent-soft) 60%, transparent);
  vertical-align: middle;
}
.step-thinking {
  margin-top: 2px;
}
.step-thinking summary {
  cursor: pointer;
  list-style: none;
  color: var(--dim);
  font-size: 12.5px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 100%;
}
.step-thinking summary::before { content: "▸ 思考："; color: var(--accent); font-weight: 600; }
.step-thinking[open] summary::before { content: "▾ 思考："; }
.step-thinking summary:hover { color: var(--accent); }
.think-summary-text { vertical-align: middle; }
.think-live {
  margin-left: 6px;
  font-size: 11px;
  color: var(--accent);
  background: color-mix(in srgb, var(--accent-soft) 50%, transparent);
  padding: 1px 6px;
  border-radius: 8px;
  animation: think-pulse 1.4s ease-in-out infinite;
}
@keyframes think-pulse {
  0%, 100% { opacity: 0.55; }
  50% { opacity: 1; }
}
.thinking-pre.streaming {
  border-left-style: solid;
}
.thinking-pre {
  margin: 4px 0 0;
  padding: 8px 10px;
  border-left: 2px dashed var(--accent);
  background: color-mix(in srgb, var(--accent-soft) 30%, transparent);
  color: var(--dim);
  font-family: var(--font-mono);
  font-size: 12px;
  line-height: 1.7;
  white-space: pre-wrap;
  word-break: break-word;
  border-radius: 0 4px 4px 0;
  max-height: 500px;
  overflow-y: auto;
}

.step-name { color: var(--accent); font-weight: 600; }
.step-running {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  margin-left: 8px;
  padding: 1px 8px;
  font-size: 10.5px;
  letter-spacing: .08em;
  text-transform: uppercase;
  border-radius: 999px;
  background: color-mix(in srgb, var(--accent) 12%, transparent);
  border: 1px solid color-mix(in srgb, var(--accent) 35%, transparent);
  color: var(--accent);
  vertical-align: middle;
}
.run-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--accent);
  animation: run-pulse 1s ease-in-out infinite;
}
.run-text { opacity: .9; }
@keyframes run-pulse {
  0%, 100% { opacity: .3; transform: scale(.8); }
  50% { opacity: 1; transform: scale(1.15); }
}
.step-args {
  color: var(--faint);
  display: block;
  white-space: pre-wrap;
  word-break: break-word;
}
.step-progress {
  margin: 6px 0 2px 14px;
  padding: 8px 10px;
  border-left: 2px dashed var(--accent-soft);
  background: color-mix(in srgb, var(--accent) 7%, transparent);
  border-radius: 4px;
  font-size: 12.5px;
  display: flex;
  flex-direction: column;
  gap: 3px;
  max-height: 260px;
  overflow-y: auto;
}
.progress-line {
  display: flex;
  align-items: flex-start;
  gap: 6px;
  line-height: 1.5;
  animation: rise .25s ease both;
}
.progress-line.latest {
  color: var(--accent);
  font-weight: 500;
}
.progress-line.latest .progress-bullet {
  animation: run-pulse 1s ease-in-out infinite;
}
.progress-bullet {
  color: var(--accent);
  flex-shrink: 0;
  font-weight: 700;
  line-height: 1.4;
  transform: translateY(-1px);
}
.progress-text {
  color: var(--text);
  word-break: break-word;
  opacity: .92;
}

.step-result-wrap {
  margin-top: 4px;
}

/* 工具下载的媒体内联播放（012）：video/img/audio */
.step-media {
  margin: 6px 0 4px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.step-video {
  max-width: 100%;
  max-height: 360px;
  border-radius: 8px;
  background: #000;
}
.step-img {
  max-width: 100%;
  border-radius: 8px;
}
.step-audio {
  width: 100%;
  max-width: 360px;
}
.step-result {
  border-left: 2px solid var(--line);
  padding: 4px 10px;
  color: var(--dim);
  font-size: 12.5px;
  white-space: pre-wrap;
  word-break: break-word;
}
.step-result > summary {
  cursor: pointer;
  color: var(--faint);
  list-style: none;
  min-height: 20px;
  font-size: 11px;
  font-family: var(--font-display);
  letter-spacing: .06em;
  padding: 2px 0;
}
.step-result > summary::before { content: "▸ "; color: var(--accent); font-weight: 600; }
.step-result[open] > summary::before { content: "▾ "; }
.step-result > summary:hover { color: var(--accent); }
.step-result-content {
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 400px;
  overflow-y: auto;
  padding-top: 4px;
}

@media (max-width: 640px) {
  .trace { max-width: 100%; }
  .trace-summary { display: none; }
}
</style>
