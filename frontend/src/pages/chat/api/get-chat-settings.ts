import { http } from '@/shared/api/http'
import { handleApiFailure } from '@/shared/lib/api-response'
import { redirectToServerPath } from '@/shared/lib/resolve-server-redirect'

import type {
  ChatSettingsData,
  ChatSettingsResponse,
} from '@/pages/chat/model/types'

export async function getChatSettings(chatId: string): Promise<ChatSettingsData> {
  try {
    const { data } = await http.get<ChatSettingsResponse>(`/chat/${chatId}/settings`)

    if (!data.ok) {
      redirectToServerPath(data.redirect)
      throw new Error(data.message)
    }

    return data
  } catch (error) {
    handleApiFailure(error, 'Не удалось загрузить настройки группы.')
  }
}
