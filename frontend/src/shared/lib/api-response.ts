import { isAxiosError } from 'axios'

import { redirectToServerPath } from '@/shared/lib/resolve-server-redirect'

type ErrorPayload = {
  message?: string
  redirect?: string
}

function fallbackMessage(defaultMessage: string, statusCode?: number) {
  if (statusCode === 404) {
    return 'API не найден. Скорее всего, серверная часть или dev-сервер не были перезапущены после изменений.'
  }

  if (statusCode === 401) {
    return 'Сессия истекла. Нужен повторный вход через страницу авторизации.'
  }

  if (statusCode === 403) {
    return 'Доступ к этому разделу запрещён.'
  }

  return defaultMessage
}

export function handleApiFailure(error: unknown, defaultMessage: string): never {
  if (isAxiosError(error)) {
    const payload = error.response?.data as ErrorPayload | undefined
    redirectToServerPath(payload?.redirect)
    throw new Error(payload?.message ?? fallbackMessage(defaultMessage, error.response?.status))
  }

  throw error
}
