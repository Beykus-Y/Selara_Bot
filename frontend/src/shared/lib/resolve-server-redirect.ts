import { resolveAppPath } from '@/shared/config/app-base-path'

export function resolveServerRedirect(url: string | undefined): string | undefined {
  if (!url) {
    return undefined
  }

  if (url.startsWith('http://') || url.startsWith('https://')) {
    return url
  }

  return resolveAppPath(url)
}

export function redirectToServerPath(url: string | undefined) {
  const resolvedUrl = resolveServerRedirect(url)

  if (resolvedUrl) {
    window.location.href = resolvedUrl
  }
}
