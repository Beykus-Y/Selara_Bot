function normalizeBasePath(value: string | undefined): string {
  if (!value || value === '/') {
    return ''
  }

  const prefixed = value.startsWith('/') ? value : `/${value}`
  return prefixed.endsWith('/') ? prefixed.slice(0, -1) : prefixed
}

export const appBasePath = normalizeBasePath(import.meta.env.BASE_URL)

export function resolveAppPath(path: string): string {
  if (!path) {
    return path
  }

  if (
    path.startsWith('http://') ||
    path.startsWith('https://') ||
    path.startsWith('//') ||
    path.startsWith('#') ||
    path.startsWith('?')
  ) {
    return path
  }

  if (!path.startsWith('/')) {
    return path
  }

  if (!appBasePath) {
    return path
  }

  if (path === appBasePath || path.startsWith(`${appBasePath}/`)) {
    return path
  }

  return `${appBasePath}${path}`
}
