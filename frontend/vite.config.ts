import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import path from 'path'

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src')
    }
  },
  server: {
    port: 5173,
    proxy: {
      // 统一以 /api 前缀代理到后端，避免跨域
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true
      }
    }
  }
})
