import { http } from '@/shared/api/http'
import { handleApiFailure } from '@/shared/lib/api-response'
import { redirectToServerPath } from '@/shared/lib/resolve-server-redirect'

import type { AuditPageData, AuditPageResponse } from '@/pages/audit/model/types'

export async function getAuditPage(chatId: string): Promise<AuditPageData> {
  try {
    const { data } = await http.get<AuditPageResponse>(`/chat/${chatId}/audit`)

    if (!data.ok) {
      redirectToServerPath(data.redirect)
      throw new Error(data.message)
    }

    return data.page
  } catch (error) {
    handleApiFailure(error, 'Не удалось загрузить аудит.')
  }
}
