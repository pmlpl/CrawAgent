<script setup>
// 「活蛛网」canvas — 蜘蛛沿丝往复爬行，网节随指针轻轻牵动。
// 尊重 prefers-reduced-motion：降级为静态单帧。
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { useTheme } from '../composables/useTheme'

const canvas = ref(null)
const { theme } = useTheme()

const SPOKES = 12, RINGS = 5, RMAX = 210
let ctx = null, W = 0, H = 0, CX = 0, CY = 0
let t = 0, raf = null, reduced = false
const pointer = { x: -999, y: -999 }

function node(s, r, wobble) {
  const a = (s / SPOKES) * Math.PI * 2
  const rad = (r / RINGS) * RMAX
  const x = CX + Math.cos(a) * rad
  const y = CY + Math.sin(a) * rad * 0.82
  const dx = x - pointer.x, dy = y - pointer.y
  const d = Math.hypot(dx, dy)
  const pull = d < 90 ? (1 - d / 90) * 10 : 0
  const wob = wobble ? Math.sin(t / 40 + s * 1.7 + r * 2.3) * 2.2 : 0
  return [x + (dx / (d || 1)) * pull + wob, y + (dy / (d || 1)) * pull + wob]
}

function accent() {
  return getComputedStyle(document.documentElement).getPropertyValue('--accent').trim() || '#3AD8C2'
}

function draw() {
  ctx.clearRect(0, 0, W, H)
  const color = accent()
  ctx.lineWidth = 1

  // 蛛丝
  ctx.strokeStyle = color
  ctx.globalAlpha = 0.16
  for (let s = 0; s < SPOKES; s++) {
    ctx.beginPath()
    ctx.moveTo(CX, CY)
    const [x, y] = node(s, RINGS, true)
    ctx.lineTo(x, y)
    ctx.stroke()
  }
  for (let r = 1; r <= RINGS; r++) {
    ctx.beginPath()
    for (let s = 0; s <= SPOKES; s++) {
      const [x, y] = node(s % SPOKES, r, true)
      s === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y)
    }
    ctx.stroke()
  }

  // 网节
  ctx.globalAlpha = 0.85
  for (let r = 1; r <= RINGS; r++) {
    for (let s = 0; s < SPOKES; s++) {
      const [x, y] = node(s, r, true)
      ctx.beginPath()
      ctx.arc(x, y, r === RINGS ? 2.6 : 1.7, 0, Math.PI * 2)
      ctx.fillStyle = color
      ctx.fill()
    }
  }

  // 蜘蛛：沿某根丝爬出再爬回
  if (!reduced) {
    const prog = (t % 240) / 240
    const along = prog < 0.5 ? prog * 2 : (1 - prog) * 2
    const spoke = Math.floor(t / 240) % SPOKES
    const [x, y] = node(spoke, 1 + along * (RINGS - 1), true)
    ctx.globalAlpha = 1
    ctx.beginPath()
    ctx.arc(x, y, 5, 0, Math.PI * 2)
    ctx.fillStyle = color
    ctx.shadowColor = color
    ctx.shadowBlur = 14
    ctx.fill()
    ctx.shadowBlur = 0
  }

  ctx.globalAlpha = 1
  t++
  if (!reduced) raf = requestAnimationFrame(draw)
}

function onMove(ev) {
  const rect = canvas.value.getBoundingClientRect()
  pointer.x = (ev.clientX - rect.left) * (W / rect.width)
  pointer.y = (ev.clientY - rect.top) * (H / rect.height)
}
function onLeave() { pointer.x = pointer.y = -999 }

// 主题切换时静态模式需重绘一帧；动态模式下一帧自动取新色
let stopWatch = null
onMounted(() => {
  reduced = matchMedia('(prefers-reduced-motion: reduce)').matches
  const c = canvas.value
  W = c.width; H = c.height; CX = W / 2; CY = H / 2
  ctx = c.getContext('2d')
  draw()
})

onBeforeUnmount(() => { if (raf) cancelAnimationFrame(raf) })
</script>

<template>
  <canvas
    ref="canvas"
    class="web-canvas"
    width="640"
    height="480"
    aria-hidden="true"
    :data-theme="theme"
    @pointermove="onMove"
    @pointerleave="onLeave"
    @click="reduced && draw()"
  />
</template>

<style scoped>
.web-canvas {
  width: min(320px, 70vw);
  height: auto;
  display: block;
  margin: 0 auto 6px;
}
</style>
