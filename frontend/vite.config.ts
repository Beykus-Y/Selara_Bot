import path from 'node:path'
import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, __dirname, '')
  const backendUrl = env.VITE_BACKEND_URL || 'http://127.0.0.1:8080'

  return {
    plugins: [react()],
    resolve: {
      alias: {
        '@': path.resolve(__dirname, './src'),
      },
    },
    server: {
      proxy: {
        '/backend': {
          target: backendUrl,
          changeOrigin: true,
          rewrite: (sourcePath) => sourcePath.replace(/^\/backend/, ''),
        },
        '/api': {
          target: backendUrl,
          changeOrigin: true,
        },
        '/login': {
          target: backendUrl,
          changeOrigin: true,
        },
        '/logout': {
          target: backendUrl,
          changeOrigin: true,
        },
        '/static': {
          target: backendUrl,
          changeOrigin: true,
        },
      },
    },
  }
})
