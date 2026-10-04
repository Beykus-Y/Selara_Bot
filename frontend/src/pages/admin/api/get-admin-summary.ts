import type { AxiosRequestConfig } from 'axios'

import { getMiniAppData } from '@/shared/miniapp/api'

export type AdminSummaryData = {
  errors_24h: number
  new_feedback_24h: number
  open_feedback: number
  recent_events: Array<{
    id: number
    severity: string
    source: string
    message: string
    created_at: string
  }>
}

export function getAdminSummary(config?: AxiosRequestConfig) {
  return getMiniAppData<AdminSummaryData>('/miniapp/admin/summary', 'Не удалось загрузить оперативную сводку.', config)
}
