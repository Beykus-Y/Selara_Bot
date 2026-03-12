import { http } from '@/shared/api/http'
import { handleApiFailure } from '@/shared/lib/api-response'
import { redirectToServerPath } from '@/shared/lib/resolve-server-redirect'

import type { FamilyPageData, FamilyPageResponse } from '@/pages/family/model/types'

export async function getFamilyPage(chatId: string, userId?: string | null): Promise<FamilyPageData> {
  try {
    const { data } = await http.get<FamilyPageResponse>(`/chat/${chatId}/family`, {
      params: userId ? { user_id: userId } : undefined,
    })

    if (!data.ok) {
      redirectToServerPath(data.redirect)
      throw new Error(data.message)
    }

    return data.page
  } catch (error) {
    handleApiFailure(error, 'Не удалось загрузить семейный граф.')
  }
}
