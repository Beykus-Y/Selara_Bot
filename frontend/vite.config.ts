import path from 'node:path'
import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, __dirname, '')
  const backendUrl = env.VITE_BACKEND_URL || 'http://127.0.0.1:8080'
  const basePath = env.VITE_APP_BASE_PATH || '/'
  const normalizedBasePath = basePath.endsWith('/') ? basePath.slice(0, -1) || '/' : basePath
  const proxyPrefix = normalizedBasePath === '/' ? '' : normalizedBasePath

  return {
    base: basePath,
    plugins: [react()],
    resolve: {
      alias: {
        '@': path.resolve(__dirname, './src'),
      },
    },
    server: {
      proxy: {
        [`${proxyPrefix}/backend`]: {
          target: backendUrl,
          changeOrigin: true,
          rewrite: (sourcePath) => sourcePath.replace(new RegExp(`^${proxyPrefix}/backend`), ''),
        },
        [`${proxyPrefix}/api`]: {
          target: backendUrl,
          changeOrigin: true,
          rewrite: (sourcePath) => sourcePath.replace(new RegExp(`^${proxyPrefix}`), ''),
        },
        [`${proxyPrefix}/logout`]: {
          target: backendUrl,
          changeOrigin: true,
          rewrite: (sourcePath) => sourcePath.replace(new RegExp(`^${proxyPrefix}`), ''),
        },
        [`${proxyPrefix}/static`]: {
          target: backendUrl,
          changeOrigin: true,
          rewrite: (sourcePath) => sourcePath.replace(new RegExp(`^${proxyPrefix}`), ''),
        },
      },
    },
  }
})
