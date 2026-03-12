import { http } from '@/shared/api/http'
import { handleApiFailure } from '@/shared/lib/api-response'
import { redirectToServerPath } from '@/shared/lib/resolve-server-redirect'

import type { GamesPageData, GamesPageResponse } from '@/pages/games/model/types'

export async function getGamesPage(): Promise<GamesPageData> {
  try {
    const { data } = await http.get<GamesPageResponse>('/app/games')

    if (!data.ok) {
      redirectToServerPath(data.redirect)
      throw new Error(data.message)
    }

    return data.page
  } catch (error) {
    handleApiFailure(error, 'Не удалось загрузить страницу игр.')
  }
}
