import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// 开发时 vite dev server (5173) 把 /api 与 /ws 代理到 FastAPI (8006)，
// 生产时 npm run build 产物 web/dist 由 FastAPI 直接托管。
export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    proxy: {
      '/api': 'http://127.0.0.1:8006',
      '/ws': { target: 'ws://127.0.0.1:8006', ws: true },
    },
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
  },
})
