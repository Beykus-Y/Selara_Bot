import path from 'node:path'
import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

function rewriteProxyLocation(
  proxyPrefix: string,
  backendUrl: string,
): (proxy: {
  on: (
    event: 'proxyRes',
    handler: (proxyRes: { headers: Record<string, string | string[] | undefined> }) => void,
  ) => void
}) => void {
  const backendOrigin = new URL(backendUrl).origin

  return (proxy) => {
    proxy.on('proxyRes', (proxyRes) => {
      const locationHeader = proxyRes.headers.location

      if (!proxyPrefix || proxyPrefix === '/' || typeof locationHeader !== 'string') {
        return
      }

      let rewrittenLocation = locationHeader

      if (locationHeader.startsWith('/')) {
        rewrittenLocation = locationHeader.startsWith(`${proxyPrefix}/`) ? locationHeader : `${proxyPrefix}${locationHeader}`
      } else {
        try {
          const parsedLocation = new URL(locationHeader)
          if (parsedLocation.origin === backendOrigin) {
            const nextPath = `${parsedLocation.pathname}${parsedLocation.search}${parsedLocation.hash}`
            rewrittenLocation = nextPath.startsWith(`${proxyPrefix}/`) ? nextPath : `${proxyPrefix}${nextPath}`
          }
        } catch {
          return
        }
      }

      proxyRes.headers.location = rewrittenLocation
    })
  }
}

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, __dirname, '')
  const backendUrl = env.VITE_BACKEND_URL || 'http://127.0.0.1:8080'
  const basePath = env.VITE_APP_BASE_PATH || '/'
  const normalizedBasePath = basePath.endsWith('/') ? basePath.slice(0, -1) || '/' : basePath
  const proxyPrefix = normalizedBasePath === '/' ? '' : normalizedBasePath
  const configureLocationRewrite = rewriteProxyLocation(proxyPrefix, backendUrl)

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
          configure: configureLocationRewrite,
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
          configure: configureLocationRewrite,
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
