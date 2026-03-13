import { useEffect } from 'react'

const TITLE_SUFFIX = 'Selara'

function formatTitle(title: string) {
  const trimmed = title.trim()
  return trimmed ? `${trimmed} • ${TITLE_SUFFIX}` : TITLE_SUFFIX
}

export function usePageTitle(title: string) {
  useEffect(() => {
    document.title = formatTitle(title)
  }, [title])
}
