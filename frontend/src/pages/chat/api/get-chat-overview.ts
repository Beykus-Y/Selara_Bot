import { http } from '@/shared/api/http'
import { handleApiFailure } from '@/shared/lib/api-response'
import { redirectToServerPath } from '@/shared/lib/resolve-server-redirect'

import type {
  ChatOverviewData,
  ChatOverviewResponse,
} from '@/pages/chat/model/types'

export async function getChatOverview(chatId: string): Promise<ChatOverviewData> {
  try {
    const { data } = await http.get<ChatOverviewResponse>(`/chat/${chatId}/overview`)

    if (!data.ok) {
      redirectToServerPath(data.redirect)
      throw new Error(data.message)
    }

    return data
  } catch (error) {
    handleApiFailure(error, 'Не удалось загрузить обзор группы.')
  }
}
