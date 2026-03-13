import { useEffect } from 'react'

import { queryClient } from '@/app/providers/query-client'

const SESSION_STORAGE_KEY = 'selara:session-changed'
const SESSION_EVENT_NAME = 'selara:session-changed'
const authQueryKeys = [['landing-context'], ['login-context'], ['app-viewer']] as const

function refreshAuthQueries() {
  for (const queryKey of authQueryKeys) {
    void queryClient.invalidateQueries({ queryKey })
  }
}

export function notifySessionChanged() {
  refreshAuthQueries()

  if (typeof window === 'undefined') {
    return
  }

  const payload = JSON.stringify({ at: Date.now() })

  try {
    window.localStorage.setItem(SESSION_STORAGE_KEY, payload)
  } catch {
    // Storage may be unavailable in hardened browser contexts.
  }

  window.dispatchEvent(new CustomEvent(SESSION_EVENT_NAME))
}

export function SessionSync() {
  useEffect(() => {
    const handleRefresh = () => {
      refreshAuthQueries()
    }

    const handleVisibilityChange = () => {
      if (document.visibilityState === 'visible') {
        refreshAuthQueries()
      }
    }

    const handleStorage = (event: StorageEvent) => {
      if (event.key === SESSION_STORAGE_KEY) {
        refreshAuthQueries()
      }
    }

    window.addEventListener('focus', handleRefresh)
    window.addEventListener('online', handleRefresh)
    window.addEventListener(SESSION_EVENT_NAME, handleRefresh)
    window.addEventListener('storage', handleStorage)
    document.addEventListener('visibilitychange', handleVisibilityChange)

    return () => {
      window.removeEventListener('focus', handleRefresh)
      window.removeEventListener('online', handleRefresh)
      window.removeEventListener(SESSION_EVENT_NAME, handleRefresh)
      window.removeEventListener('storage', handleStorage)
      document.removeEventListener('visibilitychange', handleVisibilityChange)
    }
  }, [])

  return null
}
