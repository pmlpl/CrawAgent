import { createApp } from 'vue'
import App from './App.vue'
import router from './router'
import './style.css'
import { useChat } from './composables/useChat'
import { useSettings } from './composables/useSettings'

const app = createApp(App).use(router)

// 启动时一次性加载设置 + 会话列表（不阻塞 UI）
const settings = useSettings()
const chat = useChat()
settings.load().catch(() => { /* 忽略加载失败，后端未启动时正常 */ })
chat.fetchSessions().catch(() => { /* ignore */ })

app.mount('#app')
