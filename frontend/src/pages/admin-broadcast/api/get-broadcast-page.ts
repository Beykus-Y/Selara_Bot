import { http } from '@/shared/api/http'
import { handleApiFailure } from '@/shared/lib/api-response'
import { redirectToServerPath } from '@/shared/lib/resolve-server-redirect'

import type { BroadcastPageData, BroadcastPageResponse } from '@/pages/admin-broadcast/model/types'

export async function getBroadcastPage(broadcastId: string): Promise<BroadcastPageData> {
  try {
    const { data } = await http.get<BroadcastPageResponse>(`/admin/broadcasts/${broadcastId}`)

    if (!data.ok) {
      redirectToServerPath(data.redirect)
      throw new Error(data.message)
    }

    return data.page
  } catch (error) {
    handleApiFailure(error, 'Не удалось загрузить рассылку.')
  }
}
