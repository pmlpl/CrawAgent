<script setup>
/**
 * ContextRing — 上下文余量环
 *
 * 一个轻量 SVG 圆环，显示当前会话传给 LLM 的 token 使用率。
 * 轮询 /api/sessions/{id}/context，只在聊天页且有 session 时工作。
 *
 * 轮询节奏（避免空闲刷屏/空转 DB）：
 *   - 会话打开：拉一次
 *   - 轮次在跑（useChat.busy）：每 pollInterval（默认 3s）轮询
 *   - 轮次结束：拉一次最终值再停，空闲不轮询
 *
 * 颜色分级：
 *   < 60%  绿色（安全）
 *   60-85% 黄色（注意）
 *   > 85% 红色（危险，快满了）
 */
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useChat } from '../composables/useChat'

const chat = useChat() // 单例：读 busy 判断轮次是否在跑，决定是否轮询

const props = defineProps({
  sessionId: { type: String, default: '' },
  pollInterval: { type: Number, default: 3000 },
  size: { type: Number, default: 28 },
})

// —— 状态 ——
const used = ref(0)
const limit = ref(0)
const turnCount = ref(0)
let timer = null

// —— 派生 ——
const percentage = computed(() => {
  if (!limit.value) return 0
  return Math.min(100, Math.round((used.value / limit.value) * 100))
})

const color = computed(() => {
  const p = percentage.value
  if (p >= 85) return 'var(--danger, #ef4444)'
  if (p >= 60) return '#eab308'
  return '#22c55e'
})

const strokeDasharray = computed(() => {
  const circumference = 2 * Math.PI * 12  // r=12 (size=28, stroke-width=3 → 12)
  const filled = (percentage.value / 100) * circumference
  return `${filled} ${circumference}`
})

const tooltip = computed(() => {
  if (!limit.value) return '上下文未统计'
  const usedK = (used.value / 1000).toFixed(1) + 'K'
  const limitK = (limit.value / 1000).toFixed(0) + 'K'
  return `上下文: ${usedK} / ${limitK} (${percentage.value}%)${turnCount.value ? ` · ${turnCount.value} 轮` : ''}`
})

// —— 轮询 ——
async function fetchContext() {
  if (!props.sessionId) return
  try {
    const res = await fetch(`/api/sessions/${encodeURIComponent(props.sessionId)}/context`)
    if (res.ok) {
      const data = await res.json()
      used.value = data.used || 0
      limit.value = data.limit || 0
      turnCount.value = data.turn_count || 0
    }
  } catch {
    // 静默失败 — 网络波动不影响 UI
  }
}

function startInterval() {
  stopInterval()
  timer = setInterval(fetchContext, props.pollInterval)
}

function stopInterval() {
  if (timer) {
    clearInterval(timer)
    timer = null
  }
}

// —— 生命周期 ——
// 会话切换：拉一次；若此刻有轮次在跑，开 3s 轮询
watch(() => props.sessionId, (id) => {
  if (!id) { stopInterval(); return }
  fetchContext()
  if (chat.busy) startInterval()
}, { immediate: true })

// 轮次在跑 → 3s 轮询；轮次结束 → 拉一次最终值再停（空闲不轮询）
watch(() => chat.busy, (running) => {
  if (!props.sessionId) return
  if (running) startInterval()
  else { fetchContext(); stopInterval() }
})

onBeforeUnmount(stopInterval)
</script>

<template>
  <div
    v-if="sessionId && limit"
    class="context-ring"
    :title="tooltip"
    role="img"
    :aria-label="tooltip"
  >
    <svg :width="size" :height="size" viewBox="0 0 28 28">
      <!-- 背景环 -->
      <circle
        cx="14" cy="14" r="12"
        fill="none"
        stroke="var(--line, #e5e7eb)"
        stroke-width="3"
      />
      <!-- 进度环 -->
      <circle
        cx="14" cy="14" r="12"
        fill="none"
        :stroke="color"
        stroke-width="3"
        stroke-linecap="round"
        :stroke-dasharray="strokeDasharray"
        transform="rotate(-90 14 14)"
        class="progress"
      />
      <!-- 中心百分比文字 -->
      <text
        x="14" y="14"
        text-anchor="middle"
        dominant-baseline="central"
        :fill="color"
        font-family="var(--font-mono, monospace)"
        font-size="8"
        font-weight="600"
      >
        {{ percentage }}%
      </text>
    </svg>
  </div>
</template>

<style scoped>
.context-ring {
  flex: none;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  cursor: help;
  padding: 2px;
  border-radius: 50%;
  transition: background .15s;
}
.context-ring:hover {
  background: var(--hover, rgba(0,0,0,.04));
}
.context-ring svg {
  display: block;
}
.progress {
  transition: stroke-dasharray .4s ease, stroke .3s ease;
}
</style>
