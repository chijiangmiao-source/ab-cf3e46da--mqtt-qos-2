import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// 构建产物由 FastAPI 挂载在 /static 下提供
export default defineConfig({
  plugins: [vue()],
  base: '/static/',
  server: {
    proxy: {
      '/api': 'http://localhost:8000'
    }
  }
})
