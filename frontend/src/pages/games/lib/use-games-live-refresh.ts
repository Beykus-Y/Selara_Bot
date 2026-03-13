import { useEffect, useRef } from 'react'

import { resolveAppPath } from '@/shared/config/app-base-path'

type UseGamesLiveRefreshOptions = {
  enabled: boolean
  gameIds: string[]
  onRefresh: () => void
}

const SSE_EVENT_NAMES = ['new_vote', 'phase_change', 'game_updated'] as const
const FALLBACK_POLL_MS = 4000
const WS_RETRY_MS = 5000

function resolveWsUrl(path: string) {
  const resolvedPath = resolveAppPath(path)
  const target = new URL(resolvedPath, window.location.href)
  target.protocol = target.protocol === 'https:' ? 'wss:' : 'ws:'
  return target.toString()
}

export function useGamesLiveRefresh({
  enabled,
  gameIds,
  onRefresh,
}: UseGamesLiveRefreshOptions) {
  const onRefreshRef = useRef(onRefresh)
  const gameIdsKey = gameIds.join('|')

  useEffect(() => {
    onRefreshRef.current = onRefresh
  }, [onRefresh])

  useEffect(() => {
    if (!enabled || typeof window === 'undefined') {
      return
    }

    const normalizedGameIds = gameIdsKey ? gameIdsKey.split('|') : []
    let sseHealthy = false
    let pollTimer = 0
    const readySockets = new Set<string>()
    const reconnectTimers = new Map<string, number>()
    const sockets = new Map<string, WebSocket>()

    const refresh = () => {
      if (document.visibilityState === 'hidden') {
        return
      }

      onRefreshRef.current()
    }

    const stopPolling = () => {
      if (pollTimer) {
        window.clearInterval(pollTimer)
        pollTimer = 0
      }
    }

    const syncPolling = () => {
      if (sseHealthy || readySockets.size > 0) {
        stopPolling()
        return
      }

      if (!pollTimer) {
        pollTimer = window.setInterval(refresh, FALLBACK_POLL_MS)
      }
    }

    const closeSocket = (gameId: string) => {
      const socket = sockets.get(gameId)
      if (socket) {
        socket.onopen = null
        socket.onmessage = null
        socket.onerror = null
        socket.onclose = null
        socket.close()
        sockets.delete(gameId)
      }
      readySockets.delete(gameId)
      const timer = reconnectTimers.get(gameId)
      if (timer) {
        window.clearTimeout(timer)
        reconnectTimers.delete(gameId)
      }
    }

    const connectSocket = (gameId: string) => {
      closeSocket(gameId)

      const socket = new WebSocket(resolveWsUrl(`/api/live/ws/game/${encodeURIComponent(gameId)}`))
      sockets.set(gameId, socket)

      socket.onopen = () => {
        readySockets.add(gameId)
        syncPolling()
      }

      socket.onmessage = () => {
        refresh()
      }

      socket.onerror = () => {
        socket.close()
      }

      socket.onclose = () => {
        readySockets.delete(gameId)
        syncPolling()

        reconnectTimers.set(
          gameId,
          window.setTimeout(() => {
            if (enabled) {
              connectSocket(gameId)
            }
          }, WS_RETRY_MS),
        )
      }
    }

    const source = new EventSource(resolveAppPath('/api/live/stream?scope=games'))
    const handleSseEvent = () => {
      refresh()
    }

    source.onopen = () => {
      sseHealthy = true
      syncPolling()
    }

    source.onerror = () => {
      sseHealthy = false
      syncPolling()
    }

    for (const eventName of SSE_EVENT_NAMES) {
      source.addEventListener(eventName, handleSseEvent)
    }

    for (const gameId of normalizedGameIds) {
      connectSocket(gameId)
    }

    const handleVisibility = () => {
      syncPolling()
      if (document.visibilityState === 'visible') {
        refresh()
      }
    }

    document.addEventListener('visibilitychange', handleVisibility)
    syncPolling()

    return () => {
      stopPolling()
      document.removeEventListener('visibilitychange', handleVisibility)
      for (const eventName of SSE_EVENT_NAMES) {
        source.removeEventListener(eventName, handleSseEvent)
      }
      source.close()
      for (const gameId of [...sockets.keys()]) {
        closeSocket(gameId)
      }
    }
  }, [enabled, gameIdsKey])
}
