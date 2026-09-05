import { createRouter, createWebHashHistory } from 'vue-router'
import ChatPage from './pages/ChatPage.vue'

const routes = [
  { path: '/', redirect: '/chat' },
  { path: '/chat', name: 'chat', component: ChatPage, meta: { title: '对话', icon: 'chat' } },
  {
    path: '/sites',
    name: 'sites',
    component: () => import('./pages/SitesPage.vue'),
    meta: { title: '站点', icon: 'sites' },
  },
  {
    // 站点档案详情页：origin 含斜杠，用 path 匹配让前端 encode 后丢给后端
    path: '/sites-detail/:origin(.*)',
    name: 'site-detail',
    component: () => import('./pages/SiteDetailPage.vue'),
    meta: { title: '站点详情', icon: 'sites' },
  },
  {
    path: '/settings',
    name: 'settings',
    component: () => import('./pages/SettingsPage.vue'),
    meta: { title: '设置', icon: 'settings' },
  },
]

const router = createRouter({
  history: createWebHashHistory(),
  routes,
})

export default router
