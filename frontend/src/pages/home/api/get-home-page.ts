import { http } from '@/shared/api/http'
import { handleApiFailure } from '@/shared/lib/api-response'
import { redirectToServerPath } from '@/shared/lib/resolve-server-redirect'

import type { HomePageData, HomePageResponse } from '../model/types'

export async function getHomePage(): Promise<HomePageData> {
  try {
    const { data } = await http.get<HomePageResponse>('/app/home')

    if (!data.ok) {
      redirectToServerPath(data.redirect)
      throw new Error(data.message)
    }

    return data.page
  } catch (error) {
    handleApiFailure(error, 'Не удалось загрузить главную страницу.')
  }
}
