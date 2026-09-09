import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5175,
    host: '0.0.0.0',
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8005',
        changeOrigin: true,
      },
      '/auth': {
        target: 'http://127.0.0.1:8005',
        changeOrigin: true,
      }
    }
  },
  preview: {
    port: 5175,
    host: '0.0.0.0',
    allowedHosts: ['cai-demo.circulants.ai'],
  }
})
