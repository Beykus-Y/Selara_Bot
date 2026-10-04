import type { AxiosRequestConfig } from 'axios'

import { getMiniAppData, postMiniAppData } from '@/shared/miniapp/api'

export type AdminPage<T> = { items: T[]; next_cursor: number | null }
export type AdminFeedbackItem = {
  id: number
  title: string
  preview: string
  status: 'open' | 'resolved'
  category: string
  user: { id: number; username: string | null; first_name: string | null }
  created_at: string
  updated_at: string
}
export type AdminFeedbackDetail = Omit<AdminFeedbackItem, 'preview' | 'category'> & { details: string }
export type AdminAlert = {
  id: number
  severity: 'error' | 'warning' | 'info'
  source: string
  fingerprint: string
  message: string
  context: Record<string, string> | null
  created_at: string
}
export type AdminAlertDetail = AdminAlert & { traceback: string }
export type AdminLog = { id: number; level: string; source: string; message: string; created_at: string }

export function getAdminFeedback(params: Record<string, string | number>, config?: AxiosRequestConfig) {
  return getMiniAppData<AdminPage<AdminFeedbackItem>>('/miniapp/admin/feedback', 'Не удалось загрузить обращения.', {
    ...config,
    params: { ...params, ...config?.params },
  })
}

export function getAdminFeedbackDetail(id: number, config?: AxiosRequestConfig) {
  return getMiniAppData<AdminFeedbackDetail>(`/miniapp/admin/feedback/${id}`, 'Не удалось открыть обращение.', config)
}

export function setAdminFeedbackStatus(id: number, status: 'resolve' | 'reopen') {
  return postMiniAppData<{ id: number; status: string }>(
    `/miniapp/admin/feedback/${id}/${status}`,
    {},
    'Не удалось обновить обращение.',
  )
}

export function getAdminAlerts(params: Record<string, string | number>, config?: AxiosRequestConfig) {
  return getMiniAppData<AdminPage<AdminAlert>>('/miniapp/admin/alerts', 'Не удалось загрузить alerts.', {
    ...config,
    params: { ...params, ...config?.params },
  })
}

export function getAdminAlertDetail(id: number, config?: AxiosRequestConfig) {
  return getMiniAppData<AdminAlertDetail>(`/miniapp/admin/alerts/${id}`, 'Не удалось открыть событие.', config)
}

export function getAdminLogs(params: Record<string, string | number>, config?: AxiosRequestConfig) {
  return getMiniAppData<AdminPage<AdminLog>>('/miniapp/admin/logs', 'Не удалось загрузить журнал.', {
    ...config,
    params: { ...params, ...config?.params },
  })
}
