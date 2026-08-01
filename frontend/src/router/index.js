import { createRouter, createWebHistory } from 'vue-router'

const routes = [
  {
    path: '/',
    redirect: '/chat',
  },
  {
    path: '/chat',
    name: 'Chat',
    component: () => import('../views/Chat.vue'),
    meta: { title: '对话' },
  },
  {
    path: '/tasks',
    name: 'Tasks',
    component: () => import('../views/Tasks.vue'),
    meta: { title: '任务' },
  },
  {
    path: '/settings',
    name: 'Settings',
    component: () => import('../views/Settings.vue'),
    meta: { title: '设置' },
  },
  {
    path: '/output',
    name: 'Output',
    component: () => import('../views/Output.vue'),
    meta: { title: '文件整理' },
  },
  {
    path: '/monitor',
    name: 'Monitor',
    component: () => import('../views/Monitor.vue'),
    meta: { title: '监控' },
  },
  {
    path: '/security',
    name: 'Security',
    component: () => import('../views/Security.vue'),
    meta: { title: '安全' },
  },
  {
    path: '/video',
    name: 'Video',
    component: () => import('../views/Video.vue'),
    meta: { title: '视频' },
  },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

export default router
