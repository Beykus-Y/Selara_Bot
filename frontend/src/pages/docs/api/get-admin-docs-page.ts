import { http } from '@/shared/api/http'
import { handleApiFailure } from '@/shared/lib/api-response'
import { redirectToServerPath } from '@/shared/lib/resolve-server-redirect'

import type { AdminDocsPageData, AdminDocsPageResponse } from '@/pages/docs/model/types'

export async function getAdminDocsPage(chatId?: string | null): Promise<AdminDocsPageData> {
  try {
    const { data } = await http.get<AdminDocsPageResponse>('/app/docs/admin', {
      params: chatId ? { chat_id: chatId } : undefined,
    })

    if (!data.ok) {
      redirectToServerPath(data.redirect)
      throw new Error(data.message)
    }

    return data.page
  } catch (error) {
    handleApiFailure(error, 'Не удалось загрузить документацию администратора.')
  }
}
