import { queryClient } from '@/app/providers/query-client'

export const SESSION_STORAGE_KEY = 'selara:session-changed'
export const SESSION_EVENT_NAME = 'selara:session-changed'

const authQueryKeys = [['landing-context'], ['login-context'], ['app-viewer']] as const

export function notifySessionChanged() {
  for (const queryKey of authQueryKeys) {
    void queryClient.invalidateQueries({ queryKey })
  }

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
