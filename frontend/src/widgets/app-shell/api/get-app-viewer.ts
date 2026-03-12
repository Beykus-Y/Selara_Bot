import { http } from '@/shared/api/http'
import { handleApiFailure } from '@/shared/lib/api-response'
import { redirectToServerPath } from '@/shared/lib/resolve-server-redirect'
import type { AppViewer, AppViewerResponse } from '@/widgets/app-shell/model/types'

export async function getAppViewer(): Promise<AppViewer> {
  try {
    const { data } = await http.get<AppViewerResponse>('/app/me')

    if (!data.ok) {
      redirectToServerPath(data.redirect)
      throw new Error(data.message)
    }

    return data.viewer
  } catch (error) {
    handleApiFailure(error, 'Не удалось загрузить профиль пользователя.')
  }
}
