import { shallowRef } from 'vue'

// 自定义聊天背景：图片存**服务端** data/（ADR-0001），本组合式只管状态与 CSS 应用。
// localStorage 已弃用——它按 origin 隔离（localhost:8006 与 127.0.0.1:8006 互不相通，
// 换浏览器也不共享），且有 ~5MB 配额坑；服务端存储让任何入口看到同一份壁纸。
// App.vue 启动时首次调用即从 /api/background 恢复（否则刷新进聊天页背景会丢）；
// 设置页上传/清除/调浓度走同一份状态。
// 可读性双保险：
//   1. --app-overlay 遮罩浓度可调（color-mix 引用 var(--bg)，自动跟随明暗主题）
//   2. html.bg-active 类让全局样式把半透明表面（用户气泡/报错条/轨迹卡…）实底化，杜绝透字
const bgImage = shallowRef('')   // '' = 无背景；否则为 /api/background/image?t=… 或上传时的预览 dataURL
const bgOpacity = shallowRef(60) // 遮罩浓度：--bg 占比%（0=图片全显，95=几乎只见底色）；毛玻璃卡片下不需要深罩
let initialized = false
let opacityTimer = null

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

async function fetchState() {
  try {
    const res = await fetch('/api/background')
    if (!res.ok) return
    const data = await res.json()
    if (data.image) bgImage.value = data.image
    if (Number.isFinite(data.opacity) && data.opacity >= 0 && data.opacity <= 95) bgOpacity.value = data.opacity
    applyBg()
  } catch (e) {
    // 后端不可达（异常场景）：保持无背景，不打断页面
  }
}

export function useBg() {
  if (!initialized) {
    initialized = true
    fetchState()
  }

  // 设为 '' 即清除。返回 false = 服务端保存失败：本轮内存生效、刷新即丢，调用方须显式提示。
  async function setBg(dataUrl) {
    bgImage.value = dataUrl || ''
    applyBg()
    try {
      const res = await fetch('/api/background', dataUrl
        ? {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ data_url: dataUrl }),
          }
        : { method: 'DELETE' })
      if (!res.ok) return false
      const data = await res.json().catch(() => ({}))
      // 落服务端成功后把状态切到正式 URL（带 mtime 防缓存），后续刷新/换浏览器一致
      if (data.image) {
        bgImage.value = data.image
        applyBg()
      }
      return true
    } catch (e) {
      return false
    }
  }

  function setBgOpacity(v) {
    const n = Math.round(Number(v))
    if (!Number.isFinite(n)) return
    bgOpacity.value = Math.min(95, Math.max(0, n))
    applyBg()
    // 拖动滑块高频触发：本地即时生效，防抖 500ms 后才落服务端
    clearTimeout(opacityTimer)
    opacityTimer = setTimeout(() => {
      fetch('/api/background/opacity', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ opacity: bgOpacity.value }),
      }).catch(e => console.warn('[useBg] 遮罩浓度保存失败（本轮内存生效）', e))
    }, 500)
  }

  return { bgImage, bgOpacity, setBg, setBgOpacity }
}
