import { createApp } from 'vue'
import { createPinia } from 'pinia'
import App from './App.vue'
import router from './router'
import './style.css'
import { useChat } from './composables/useChat'
import { useSettings } from './composables/useSettings'

// Pinia 必须在 useChat()（触发 store 创建）前装，否则 getActivePinia() 抛错
const app = createApp(App).use(createPinia()).use(router)

// 启动时一次性加载设置 + 会话列表（不阻塞 UI）
const settings = useSettings()
const chat = useChat()
settings.load().catch(() => { /* 忽略加载失败，后端未启动时正常 */ })
chat.fetchSessions().catch(() => { /* ignore */ })

app.mount('#app')
