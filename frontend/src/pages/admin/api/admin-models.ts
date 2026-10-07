import { isAxiosError } from 'axios'
import { http } from '@/shared/api/http'
import { getMiniAppData } from '@/shared/miniapp/api'

export type Capabilities = { supports_tools: boolean; supports_structured_output: boolean; supports_vision: boolean }
export type CatalogModel = {
  key: string; model_id: string; display_name: string; enabled: boolean
  prompt_price_usd_per_million: string | null; completion_price_usd_per_million: string | null
  capabilities: Capabilities; aliases: string[]; revision: number; used_by_profiles: string[]
}
export type ModelProfile = {
  profile_key: string; display_name: string; model_key: string | null; enabled: boolean
  ail_multiplier: string; revision: number; assigned_model: CatalogModel | null; effective_pricing: CatalogModel | null
  effective: { model_id: string; is_fallback: boolean; capabilities: Capabilities | null }
}
export const getAdminModels = () => getMiniAppData<{ items: CatalogModel[] }>(
  '/miniapp/admin/ai/models', 'Не удалось загрузить каталог моделей.',
)
export const getAdminProfiles = () => getMiniAppData<{ items: ModelProfile[]; fallback_note: string }>(
  '/miniapp/admin/ai/model-profiles', 'Не удалось загрузить профили.',
)
async function save(url: string, payload: unknown, create = false) {
  try { await http.request({ url, method: create ? 'POST' : 'PUT', data: payload }) }
  catch (error) {
    if (isAxiosError(error)) {
      const data = error.response?.data
      const message = typeof data?.message === 'string' && data.message.trim()
        ? data.message
        : typeof data?.detail === 'string' && data.detail.trim() ? data.detail : null
      throw new Error(message ?? 'Проверьте поля формы. Не удалось сохранить конфигурацию.')
    }
    throw error
  }
}
export function saveAdminModel(key: string | null, payload: unknown) {
  return save(`/miniapp/admin/ai/models${key ? `/${encodeURIComponent(key)}` : ''}`, payload, key === null)
}
export function saveAdminProfile(key: string, payload: unknown) {
  return save(`/miniapp/admin/ai/model-profiles/${encodeURIComponent(key)}`, payload)
}

export type QuotaMode = {
  quota_mode: 'requests' | 'ail'
  free_daily_ail: number | null
  paid_daily_ail: number | null
  requests: { free_daily: number; paid_daily: number }
  max_daily_ail: number
  activation_problems: string[]
  profiles: Array<{ profile_key: string; display_name: string; ail_multiplier: string; available: boolean }>
  applies_within_seconds: number
}
export const getQuotaMode = () => getMiniAppData<QuotaMode>(
  '/miniapp/admin/monetization/quota-mode', 'Не удалось загрузить систему лимитов.',
)
export class ConfirmationRequired extends Error {}
export async function saveQuotaMode(payload: {
  quota_mode: 'requests' | 'ail'; free_daily_ail: number | null; paid_daily_ail: number | null; confirm?: boolean
}) {
  try { await http.request({ url: '/miniapp/admin/monetization/quota-mode', method: 'PUT', data: payload }) }
  catch (error) {
    if (isAxiosError(error)) {
      const detail = error.response?.data?.detail ?? error.response?.data?.message
      const message = typeof detail === 'string' && detail.trim() ? detail : 'Не удалось сохранить систему лимитов.'
      if (error.response?.status === 409) throw new ConfirmationRequired(message)
      throw new Error(message)
    }
    throw error
  }
}

export type FeatureRoute = {
  route_key: string; title: string; profile_key: string | null; effective_model_id: string; is_fallback: boolean
}
export const getFeatureRoutes = () => getMiniAppData<{
  items: FeatureRoute[]; profiles: Array<{ profile_key: string; display_name: string }>
  applies_within_seconds: number; fallback_note: string
}>('/miniapp/admin/ai/feature-routes', 'Не удалось загрузить модели для групп.')
export function saveFeatureRoute(routeKey: string, profileKey: string | null) {
  return save(`/miniapp/admin/ai/feature-routes/${encodeURIComponent(routeKey)}`, { profile_key: profileKey })
}
