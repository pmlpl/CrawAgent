import { ref } from 'vue'

// 主题单例：html[data-theme] + localStorage 持久化，默认暗色
const theme = ref('dark')
let initialized = false

export function useTheme() {
  if (!initialized) {
    initialized = true
    let saved = null
    try { saved = localStorage.getItem('crawagent-theme') } catch (e) { /* ignore */ }
    theme.value = saved === 'light' ? 'light' : 'dark'
    document.documentElement.dataset.theme = theme.value
  }

  function toggle() {
    theme.value = theme.value === 'dark' ? 'light' : 'dark'
    document.documentElement.dataset.theme = theme.value
    try { localStorage.setItem('crawagent-theme', theme.value) } catch (e) { /* ignore */ }
  }

  return { theme, toggle }
}
