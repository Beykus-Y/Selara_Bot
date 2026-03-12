import { useEffect, useRef } from 'react'

type UseNamedEventSourceOptions = {
  enabled: boolean
  path: string
  eventNames: readonly string[]
  onEvent: () => void
}

export function useNamedEventSource({
  enabled,
  path,
  eventNames,
  onEvent,
}: UseNamedEventSourceOptions) {
  const onEventRef = useRef(onEvent)

  useEffect(() => {
    onEventRef.current = onEvent
  }, [onEvent])

  useEffect(() => {
    if (!enabled || typeof window === 'undefined' || !('EventSource' in window)) {
      return
    }

    const source = new EventSource(path)
    const handleEvent = () => {
      onEventRef.current()
    }

    for (const eventName of eventNames) {
      source.addEventListener(eventName, handleEvent)
    }

    return () => {
      for (const eventName of eventNames) {
        source.removeEventListener(eventName, handleEvent)
      }
      source.close()
    }
  }, [enabled, eventNames, path])
}
