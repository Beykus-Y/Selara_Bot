import { isAxiosError } from 'axios'

import { http } from '@/shared/api/http'

import type {
  ForgetAllResult,
  PersonalMemoryItem,
  PersonalOverview,
  PersonalProfile,
  PersonalSettingsPatch,
} from '../model/types'

type Envelope<T> = ({ ok: true } & T) | { ok: false; message: string }

async function request<T>(
  method: 'get' | 'post' | 'put' | 'delete',
  url: string,
  fallback: string,
  body?: Record<string, unknown>,
  signal?: AbortSignal,
): Promise<T> {
  try {
    const { data } = await http.request<Envelope<T>>({
      method,
      url: `/miniapp/personal${url}`,
      data: body,
      signal,
      validateStatus: () => true,
    })
    if (!data || typeof data !== 'object' || !('ok' in data)) {
      throw new Error(fallback)
    }
    if (!data.ok) {
      throw new Error(data.message || fallback)
    }
    return data
  } catch (error) {
    if (isAxiosError(error) && error.code === 'ERR_CANCELED') throw error
    throw error instanceof Error ? error : new Error(fallback)
  }
}

export function getPersonalOverview(signal?: AbortSignal) {
  return request<PersonalOverview>('get', '', 'Не удалось загрузить «Мою Selara».', undefined, signal)
}

export function addPersonalMemory(content: string) {
  return request<{ status: 'added' | 'duplicate'; item?: PersonalMemoryItem }>(
    'post',
    '/memory',
    'Не удалось сохранить факт.',
    { content },
  )
}

export function deletePersonalMemory(id: number) {
  return request<Record<string, never>>('delete', `/memory/${id}`, 'Не удалось удалить факт.')
}

export function pinPersonalMemory(id: number, pinned: boolean) {
  return request<{ pinned: boolean }>('post', `/memory/${id}/pin`, 'Не удалось закрепить факт.', { pinned })
}

export function updatePersonalSettings(patch: PersonalSettingsPatch) {
  return request<{ profile: PersonalProfile }>('put', '/settings', 'Не удалось сохранить настройки.', patch)
}

export function forgetAllPersonalData() {
  return request<{ removed: ForgetAllResult }>('post', '/forget-all', 'Не удалось удалить данные.', {
    confirm: true,
  })
}
