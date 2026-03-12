import { http } from '@/shared/api/http'
import { handleApiFailure } from '@/shared/lib/api-response'
import { redirectToServerPath } from '@/shared/lib/resolve-server-redirect'

import type {
  ChatAchievementsData,
  ChatAchievementsResponse,
} from '@/pages/chat/model/types'

export async function getChatAchievements(chatId: string): Promise<ChatAchievementsData> {
  try {
    const { data } = await http.get<ChatAchievementsResponse>(`/chat/${chatId}/achievements`)

    if (!data.ok) {
      redirectToServerPath(data.redirect)
      throw new Error(data.message)
    }

    return {
      chat_id: data.chat_id,
      chat_title: data.chat_title,
      can_manage_settings: data.can_manage_settings,
      achievement_sections: data.achievement_sections,
    }
  } catch (error) {
    handleApiFailure(error, 'Не удалось загрузить достижения группы.')
  }
}
