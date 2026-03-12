import { http } from '@/shared/api/http'
import { handleApiFailure } from '@/shared/lib/api-response'
import { redirectToServerPath } from '@/shared/lib/resolve-server-redirect'

import type { UserDocsPageData, UserDocsPageResponse } from '@/pages/docs/model/types'

export async function getUserDocsPage(chatId?: string | null): Promise<UserDocsPageData> {
  try {
    const { data } = await http.get<UserDocsPageResponse>('/app/docs/user', {
      params: chatId ? { chat_id: chatId } : undefined,
    })

    if (!data.ok) {
      redirectToServerPath(data.redirect)
      throw new Error(data.message)
    }

    return data.page
  } catch (error) {
    handleApiFailure(error, 'Не удалось загрузить пользовательскую документацию.')
  }
}
