import { http } from '@/shared/api/http'
import { handleApiFailure } from '@/shared/lib/api-response'
import { redirectToServerPath } from '@/shared/lib/resolve-server-redirect'

import type { AdminPageData, AdminPageResponse } from '@/pages/admin/model/types'

export async function getAdminPage(): Promise<AdminPageData> {
  try {
    const { data } = await http.get<AdminPageResponse>('/admin')

    if (!data.ok) {
      redirectToServerPath(data.redirect)
      throw new Error(data.message)
    }

    return data.page
  } catch (error) {
    handleApiFailure(error, 'Не удалось загрузить админ-панель.')
  }
}
