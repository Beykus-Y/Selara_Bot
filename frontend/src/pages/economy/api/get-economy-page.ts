import { http } from '@/shared/api/http'
import { handleApiFailure } from '@/shared/lib/api-response'
import { redirectToServerPath } from '@/shared/lib/resolve-server-redirect'

import type { EconomyPageData, EconomyPageResponse } from '@/pages/economy/model/types'

export async function getEconomyPage(chatId: string): Promise<EconomyPageData> {
  try {
    const { data } = await http.get<EconomyPageResponse>(`/chat/${chatId}/economy`)

    if (!data.ok) {
      redirectToServerPath(data.redirect)
      throw new Error(data.message)
    }

    return data.page
  } catch (error) {
    handleApiFailure(error, 'Не удалось загрузить экономику.')
  }
}
