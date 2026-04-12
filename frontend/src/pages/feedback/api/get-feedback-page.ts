import { http } from '@/shared/api/http'
import { handleApiFailure } from '@/shared/lib/api-response'
import { redirectToServerPath } from '@/shared/lib/resolve-server-redirect'

import type { FeedbackPageData, FeedbackPageResponse } from '@/pages/feedback/model/types'

export async function getFeedbackPage(): Promise<FeedbackPageData> {
  try {
    const { data } = await http.get<FeedbackPageResponse>('/app/feedback')

    if (!data.ok) {
      redirectToServerPath(data.redirect)
      throw new Error(data.message)
    }

    return data.page
  } catch (error) {
    handleApiFailure(error, 'Не удалось загрузить страницу обратной связи.')
  }
}
