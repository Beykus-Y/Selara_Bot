import { postForm } from '@/shared/api/post-form'
import { handleApiFailure } from '@/shared/lib/api-response'

import type { FeedbackSubmitResponse } from '@/pages/feedback/model/types'

export async function submitFeedback(title: string, details: string): Promise<{ message: string; request_id?: number }> {
  try {
    const data = await postForm<FeedbackSubmitResponse>('/api/app/feedback', { title, details })

    if (!data.ok) {
      throw new Error(data.message)
    }

    return { message: data.message, request_id: data.request_id }
  } catch (error) {
    handleApiFailure(error, 'Не удалось отправить заявку.')
  }
}
