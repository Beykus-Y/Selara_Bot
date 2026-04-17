import type { AxiosRequestConfig } from 'axios'
import { isAxiosError } from 'axios'

import { http } from '@/shared/api/http'
import { postForm } from '@/shared/api/post-form'

type ErrorResponse = {
  ok: false
  message: string
}

type PageResponse<T> = {
  ok: true
  page: T
} | ErrorResponse

type DataResponse<T> = ({
  ok: true
} & T) | ErrorResponse

function normalizeMiniAppPostUrl(url: string) {
  if (url.startsWith('/api/')) {
    return url
  }

  return `/api${url}`
}

function resolveMiniAppError(error: unknown, fallbackMessage: string): Error {
  if (isAxiosError(error)) {
    const message = typeof error.response?.data?.message === 'string' ? error.response.data.message : null
    return new Error(message || fallbackMessage)
  }

  if (error instanceof Error) {
    return error
  }

  return new Error(fallbackMessage)
}

export async function getMiniAppPage<T>(
  url: string,
  fallbackMessage: string,
  config?: AxiosRequestConfig,
): Promise<T> {
  try {
    const { data } = await http.get<PageResponse<T>>(url, {
      ...config,
      validateStatus: () => true,
    })

    if (!data.ok) {
      throw new Error(data.message)
    }

    return data.page
  } catch (error) {
    throw resolveMiniAppError(error, fallbackMessage)
  }
}

export async function getMiniAppData<T>(
  url: string,
  fallbackMessage: string,
  config?: AxiosRequestConfig,
): Promise<T> {
  try {
    const { data } = await http.get<DataResponse<T>>(url, {
      ...config,
      validateStatus: () => true,
    })

    if (!data.ok) {
      throw new Error(data.message)
    }

    const { ok: _ok, ...payload } = data
    return payload as T
  } catch (error) {
    throw resolveMiniAppError(error, fallbackMessage)
  }
}

export async function postMiniAppData<T>(
  url: string,
  values: Record<string, string | number | boolean | null | undefined>,
  fallbackMessage: string,
): Promise<T> {
  try {
    const data = await postForm<DataResponse<T>>(normalizeMiniAppPostUrl(url), values)

    if (!data.ok) {
      throw new Error(data.message)
    }

    const { ok: _ok, ...payload } = data
    return payload as T
  } catch (error) {
    throw resolveMiniAppError(error, fallbackMessage)
  }
}
