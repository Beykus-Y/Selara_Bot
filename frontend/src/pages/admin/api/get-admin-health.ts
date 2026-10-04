import type { AxiosRequestConfig } from 'axios'

import { getMiniAppData } from '@/shared/miniapp/api'

import type { AdminHealthData } from '../model/types'

export function getAdminHealth(config?: AxiosRequestConfig) {
  return getMiniAppData<AdminHealthData>(
    '/miniapp/admin/health',
    'Не удалось проверить компоненты Selara.',
    config,
  )
}
