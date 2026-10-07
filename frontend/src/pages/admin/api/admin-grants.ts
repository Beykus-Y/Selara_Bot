import { isAxiosError } from 'axios'
import { http } from '@/shared/api/http'
import { getMiniAppData } from '@/shared/miniapp/api'

export type GrantScope = 'user' | 'chat'
export type GrantAction = 'grant' | 'extend' | 'revoke' | 'shorten'

export type LookupResult = {
  users: Array<{ id: number; username: string | null; name: string | null; is_bot: boolean }>
  chats: Array<{ id: number; title: string | null; type: string }>
}
export type TargetState = {
  scope: GrantScope
  target_id: number
  title: string | null
  status: string | null
  active: boolean
  valid_until: string | null
  paid_recently: boolean
  granted_by_admin: boolean
}
export type GrantRow = {
  id: number
  scope: GrantScope
  target_id: number
  target_title: string | null
  action: GrantAction
  delta_days: number
  valid_until_after: string | null
  status_after: string
  reason: string
  source: string
  notified: boolean | null
  created_at: string
}
export type PersonalEntitlement = {
  user_id: number
  username: string | null
  name: string | null
  valid_until: string
  days_left: number
  granted_by_admin: boolean
}
export type GrantResult = {
  grant_id: number
  action: GrantAction
  scope: GrantScope
  target_id: number
  status: string
  valid_until: string | null
  delta_days: number
  duplicate: boolean
  paid_recently: boolean
  notified: boolean | null
}
export type GrantRequest = {
  scope: GrantScope
  target_id: number
  days: number
  reason: string
  idempotency_key: string
  notify: boolean
}
export type RevokeRequest = {
  scope: GrantScope
  target_id: number
  mode: 'cancel_all' | 'shorten'
  days: number | null
  reason: string
  idempotency_key: string
  notify: boolean
}

const base = '/miniapp/admin/monetization'

export const lookupGrantTarget = (query: string) =>
  getMiniAppData<LookupResult>(`${base}/lookup`, 'Не удалось выполнить поиск.', { params: { q: query } })
export const getGrantTarget = (scope: GrantScope, targetId: number) =>
  getMiniAppData<TargetState>(`${base}/target`, 'Не удалось загрузить состояние подписки.', {
    params: { scope, target_id: targetId },
  })
export const getRecentGrants = () => getMiniAppData<{ items: GrantRow[] }>(`${base}/grants`, 'Не удалось загрузить журнал.', {
  params: { limit: 15 },
})
export const getPersonalEntitlements = () => getMiniAppData<{ items: PersonalEntitlement[] }>(
  `${base}/personal-entitlements`, 'Не удалось загрузить личные подписки.',
)

function readableError(error: unknown): Error {
  if (isAxiosError(error)) {
    const detail = error.response?.data?.detail
    if (detail && typeof detail === 'object' && typeof detail.message === 'string') return new Error(detail.message)
    if (Array.isArray(detail)) return new Error('Проверьте поля формы: срок 1–365 дней, причина и числовой id.')
    if (typeof detail === 'string' && detail.trim()) return new Error(detail)
    if (error.response?.status === 403) return new Error('Недостаточно прав.')
  }
  return error instanceof Error ? error : new Error('Не удалось выполнить операцию.')
}

async function post<T>(path: string, payload: unknown): Promise<T> {
  try {
    const { data } = await http.post<T>(`${base}${path}`, payload)
    return data
  } catch (error) {
    throw readableError(error)
  }
}

export const createGrant = (payload: GrantRequest) => post<GrantResult>('/grants', payload)
export const revokeGrant = (payload: RevokeRequest) => post<GrantResult>('/grants/revoke', payload)
