import { http } from '@/shared/api/http'
import { getMiniAppData } from '@/shared/miniapp/api'

export type BroadcastPreview = {
  body: string
  rendered_text: string
  reaction_options: Array<{ key: string; emoji: string; label: string }>
  active_since_days: number
  target_count: number
  targets: Array<{ chat_id: number; title: string | null; last_activity_at: string | null }>
  targets_truncated: boolean
  media: { type: 'photo'; filename: string; width: number; height: number; size: number } | null
  preview_token: string
}

export type BroadcastProgress = {
  broadcast_id: number
  status: 'sending' | 'completed' | 'interrupted'
  target_count: number
  sent_count: number
  failed_count: number
  pending_count: number
  skipped_count: number
  duration_seconds: number | null
  created_at: string
}
export type BroadcastHistoryItem = {
  id: number
  body: string
  created_at: string
  target_count: number
  sent_count: number
  failed_count: number
  pending_count: number
  media_type: string | null
}

export function getAdminBroadcastHistory(params: Record<string, string | number>, signal?: AbortSignal) {
  return getMiniAppData<{ items: BroadcastHistoryItem[]; next_cursor: number | null }>(
    '/miniapp/admin/broadcasts',
    'Не удалось загрузить историю рассылок.',
    { params, signal },
  )
}

async function postData<T>(path: string, payload: Record<string, unknown>, fallback: string): Promise<T> {
  try {
    let body: Record<string, unknown> | FormData = payload
    if (payload.photo instanceof File) {
      const form = new FormData()
      for (const [key, value] of Object.entries(payload)) {
        if (key === 'photo' && value instanceof File) form.append('photo', value)
        else if (key === 'chat_ids' && Array.isArray(value)) form.append(key, value.join(','))
        else if (value !== undefined) form.append(key, String(value))
      }
      body = form
    }
    const { data } = await http.post<T>(path, body, body instanceof FormData ? { headers: { 'Content-Type': 'multipart/form-data' } } : undefined)
    return data
  } catch (error) {
    const detail = typeof error === 'object' && error !== null && 'response' in error
      ? (error as { response?: { data?: { detail?: string } } }).response?.data?.detail
      : undefined
    throw new Error(typeof detail === 'string' ? detail : fallback)
  }
}

export async function previewAdminBroadcast(payload: { body: string; active_since_days: number; chat_ids?: number[]; media_mode?: string; photo?: File }) {
  return postData<BroadcastPreview>('/miniapp/admin/broadcast/preview', payload, 'Не удалось собрать preview рассылки.')
}

export async function startAdminBroadcast(payload: {
  body: string
  active_since_days: number
  chat_ids?: number[]
  confirm: true
  idempotency_key: string
  preview_token: string
  media_mode?: string
  photo?: File
}) {
  return postData<{ broadcast_id: number; status: string; target_count: number; duplicate?: boolean }>(
    '/miniapp/admin/broadcast',
    payload,
    'Не удалось начать рассылку.',
  )
}

export async function getAdminBroadcastProgress(id: number, signal?: AbortSignal) {
  const { data } = await http.get<BroadcastProgress>(`/miniapp/admin/broadcast/${id}`, { signal })
  return data
}
