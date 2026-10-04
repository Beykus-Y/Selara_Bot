import type { AxiosRequestConfig } from 'axios'

import { getMiniAppData } from '@/shared/miniapp/api'

import type { AdminAudienceData } from '../model/types'

export function getAdminAudience(periodDays: number, config?: AxiosRequestConfig) {
  return getMiniAppData<AdminAudienceData>(
    '/miniapp/admin/audience',
    'Не удалось загрузить аудиторию Selara.',
    { ...config, params: { ...config?.params, period_days: periodDays } },
  )
}
