import { ref } from 'vue'

// 自定义聊天背景：图片 dataURL 存 localStorage，应用到 <html> 的 CSS 变量。
// App.vue 启动时首次调用即恢复（否则刷新进聊天页背景会丢）；
// 设置页上传/清除/调浓度走同一份状态。
// 可读性双保险：
//   1. --app-overlay 遮罩浓度可调（color-mix 引用 var(--bg)，自动跟随明暗主题）
//   2. html.bg-active 类让全局样式把半透明表面（用户气泡/报错条/轨迹卡…）实底化，杜绝透字
const BG_KEY = 'crawagent-bg-image'
const OPACITY_KEY = 'crawagent-bg-opacity'
const bgImage = ref('')
const bgOpacity = ref(78) // 遮罩浓度：--bg 占比%（0=图片全显，95=几乎只见底色）
let initialized = false

function applyBg() {
  const root = document.documentElement
  if (bgImage.value) {
    root.style.setProperty('--bg-image', `url("${bgImage.value}")`)
    root.style.setProperty('--app-overlay', `color-mix(in srgb, var(--bg) ${bgOpacity.value}%, transparent)`)
    root.classList.add('bg-active')
  } else {
    root.style.removeProperty('--bg-image')
    root.style.removeProperty('--app-overlay')
    root.classList.remove('bg-active')
  }
}

export function useBg() {
  if (!initialized) {
    initialized = true
    try { bgImage.value = localStorage.getItem(BG_KEY) || '' } catch (e) { /* ignore */ }
    try {
      const v = Number(localStorage.getItem(OPACITY_KEY))
      if (Number.isFinite(v) && v >= 0 && v <= 95) bgOpacity.value = v
    } catch (e) { /* ignore */ }
    applyBg()
  }

  function setBg(dataUrl) {
    bgImage.value = dataUrl || ''
    try {
      if (bgImage.value) localStorage.setItem(BG_KEY, bgImage.value)
      else localStorage.removeItem(BG_KEY)
    } catch (e) { /* ignore quota */ }
    applyBg()
  }

  function setBgOpacity(v) {
    const n = Math.round(Number(v))
    if (!Number.isFinite(n)) return
    bgOpacity.value = Math.min(95, Math.max(0, n))
    try { localStorage.setItem(OPACITY_KEY, String(bgOpacity.value)) } catch (e) { /* ignore */ }
    applyBg()
  }

  return { bgImage, bgOpacity, setBg, setBgOpacity }
}
