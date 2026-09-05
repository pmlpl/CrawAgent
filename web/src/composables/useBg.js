import { ref } from 'vue'

// 自定义聊天背景：图片 dataURL 存 localStorage，应用到 <html> 的 CSS 变量。
// App.vue 启动时首次调用即恢复（否则刷新进聊天页背景会丢）；
// 设置页上传/清除走同一份状态。遮罩颜色引用 var(--bg)，自动跟随明暗主题。
const BG_KEY = 'crawagent-bg-image'
const bgImage = ref('')
let initialized = false

function applyBg() {
  if (bgImage.value) {
    document.documentElement.style.setProperty('--bg-image', `url("${bgImage.value}")`)
    // 半透明遮罩：背景图上覆盖一层 --bg 色，保证正文可读性
    document.documentElement.style.setProperty('--app-overlay', 'color-mix(in srgb, var(--bg) 78%, transparent)')
  } else {
    document.documentElement.style.removeProperty('--bg-image')
    document.documentElement.style.removeProperty('--app-overlay')
  }
}

export function useBg() {
  if (!initialized) {
    initialized = true
    try { bgImage.value = localStorage.getItem(BG_KEY) || '' } catch (e) { /* ignore */ }
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

  return { bgImage, setBg }
}
