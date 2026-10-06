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
      const detail = error.response?.data?.detail
      throw new Error(typeof detail === 'string' ? detail : 'Проверьте поля формы. Не удалось сохранить конфигурацию.')
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
