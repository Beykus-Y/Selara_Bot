import { useEffect } from 'react'

import { queryClient } from '@/app/providers/query-client'
import { SESSION_EVENT_NAME, SESSION_STORAGE_KEY } from '@/shared/lib/session-events'

const authQueryKeys = [['landing-context'], ['login-context'], ['app-viewer']] as const

function refreshAuthQueries() {
  for (const queryKey of authQueryKeys) {
    void queryClient.invalidateQueries({ queryKey })
  }
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
