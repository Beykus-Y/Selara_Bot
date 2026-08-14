import axios from 'axios'
import { resolveAppPath } from '@/shared/config/app-base-path'
import { postForm } from '@/shared/api/post-form'
import { handleApiFailure } from '@/shared/lib/api-response'

import type { AdminActionResponse } from '@/pages/admin/model/types'

export async function adminLogout(): Promise<void> {
  try {
    await postForm<AdminActionResponse>('/api/admin/logout', {})
  } catch (error) {
    handleApiFailure(error, 'Не удалось выйти из админки.')
  }
}

export async function adminRequestBackup(): Promise<string> {
  try {
    const data = await postForm<AdminActionResponse>('/api/admin/request-backup', {})
    if (!data.ok) throw new Error(data.message)
    return data.message
  } catch (error) {
    handleApiFailure(error, 'Не удалось запросить бекап.')
  }
}

export async function adminSendBroadcast(
  body: string,
  chatIds: number[],
  mediaMode: 'text' | 'photo',
  photo: File | null,
): Promise<{ message: string; broadcast_id: number }> {
  try {
    const form = new FormData()
    form.set('body', body)
    form.set('media_mode', mediaMode)
    for (const id of chatIds) {
      form.append('chat_ids', String(id))
    }
    if (photo) form.set('photo', photo)

    const { data } = await axios.post<AdminActionResponse>(resolveAppPath('/api/admin/broadcasts/send'), form, {
      withCredentials: true,
      headers: {
        Accept: 'application/json',
        'X-Requested-With': 'fetch',
      },
    })

    if (!data.ok) throw new Error(data.message)
    return { message: data.message, broadcast_id: (data as { ok: true; message: string; broadcast_id?: number }).broadcast_id ?? 0 }
  } catch (error) {
    handleApiFailure(error, 'Не удалось отправить рассылку.')
  }
}

export async function adminUpdateFeedbackStatus(requestId: number, status: 'open' | 'done'): Promise<string> {
  try {
    const data = await postForm<AdminActionResponse>(`/api/admin/feedback/${requestId}/status`, { status })
    if (!data.ok) throw new Error(data.message)
    return data.message
  } catch (error) {
    handleApiFailure(error, 'Не удалось обновить статус заявки.')
  }
}
