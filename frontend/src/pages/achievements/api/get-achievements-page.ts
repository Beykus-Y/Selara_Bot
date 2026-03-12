import { http } from '@/shared/api/http'
import { handleApiFailure } from '@/shared/lib/api-response'
import { redirectToServerPath } from '@/shared/lib/resolve-server-redirect'

import type {
  AchievementsPageData,
  AchievementsPageResponse,
} from '../model/types'

export async function getAchievementsPage(): Promise<AchievementsPageData> {
  try {
    const { data } = await http.get<AchievementsPageResponse>('/app/achievements')

    if (!data.ok) {
      redirectToServerPath(data.redirect)
      throw new Error(data.message)
    }

    return data.page
  } catch (error) {
    handleApiFailure(error, 'Не удалось загрузить достижения.')
  }
}
