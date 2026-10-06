import { isAxiosError } from 'axios'

import type {
  PersonalMemory,
  PersonalMemoryList,
  PersonalOverview,
  PersonalProfile,
  PersonalProfileChanges,
} from '@/pages/personal/model/types'
import { http } from '@/shared/api/http'

const BASE = '/miniapp/personal'

/** The server refused a change; ``code`` and (for a revision conflict) the current profile come along. */
export class PersonalApiError extends Error {
  code: string | null
  profile: PersonalProfile | null
  status: number | null

  constructor(message: string, options: { code?: string | null; profile?: PersonalProfile | null; status?: number | null } = {}) {
    super(message)
    this.name = 'PersonalApiError'
    this.code = options.code ?? null
    this.profile = options.profile ?? null
    this.status = options.status ?? null
  }
}

type ErrorBody = { message?: unknown; code?: unknown; profile?: unknown }

function toApiError(error: unknown, fallback: string): PersonalApiError {
  if (isAxiosError(error)) {
    const body = (error.response?.data ?? {}) as ErrorBody
    const message =
      typeof body.message === 'string' && body.message
        ? body.message
        : error.response?.status === 401
          ? 'Сессия истекла. Откройте Mini App заново.'
          : fallback
    return new PersonalApiError(message, {
      code: typeof body.code === 'string' ? body.code : null,
      profile: (body.profile as PersonalProfile | undefined) ?? null,
      status: error.response?.status ?? null,
    })
  }
  return error instanceof PersonalApiError ? error : new PersonalApiError(fallback)
}

async function call<T>(request: () => Promise<{ data: T }>, fallback: string): Promise<T> {
  try {
    return (await request()).data
  } catch (error) {
    throw toApiError(error, fallback)
  }
}

export async function getPersonalOverview(signal?: AbortSignal) {
  return call(
    () => http.get<PersonalOverview & { ok: true }>(BASE, { signal }),
    'Не удалось загрузить «Мою Selara».',
  )
}

export async function updatePersonalProfile(revision: number, changes: PersonalProfileChanges) {
  const data = await call(
    () => http.patch<{ ok: true; profile: PersonalProfile }>(`${BASE}/profile`, { revision, ...changes }),
    'Не удалось сохранить настройки.',
  )
  return data.profile
}

export async function getPersonalMemories(signal?: AbortSignal) {
  return call(
    () => http.get<PersonalMemoryList & { ok: true }>(`${BASE}/memories`, { signal }),
    'Не удалось загрузить память.',
  )
}

export async function addPersonalMemory(content: string) {
  const data = await call(
    () => http.post<{ ok: true; item: PersonalMemory; count: number }>(`${BASE}/memories`, { content }),
    'Не удалось сохранить факт.',
  )
  return data.item
}

export async function setPersonalMemoryPinned(id: number, pinned: boolean) {
  await call(() => http.patch<{ ok: true }>(`${BASE}/memories/${id}`, { pinned }), 'Не удалось закрепить факт.')
}

export async function deletePersonalMemory(id: number) {
  await call(() => http.delete<{ ok: true }>(`${BASE}/memories/${id}`), 'Не удалось удалить факт.')
}
