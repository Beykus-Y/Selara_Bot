import { http } from '@/shared/api/http'
import { handleApiFailure } from '@/shared/lib/api-response'
import { redirectToServerPath } from '@/shared/lib/resolve-server-redirect'

import type {
  ChatLeaderboardData,
  ChatLeaderboardMode,
  ChatLeaderboardResponse,
} from '@/pages/chat/model/types'

type GetChatLeaderboardParams = {
  chatId: string
  mode: ChatLeaderboardMode
  page: number
  query: string
}

export async function getChatLeaderboard({
  chatId,
  mode,
  page,
  query,
}: GetChatLeaderboardParams): Promise<ChatLeaderboardData> {
  try {
    const { data } = await http.get<ChatLeaderboardResponse>(`/chat/${chatId}/leaderboard`, {
      params: {
        mode,
        page,
        q: query,
      },
    })

    if (!data.ok) {
      redirectToServerPath(data.redirect)
      throw new Error(data.message)
    }

    return {
      mode: data.mode,
      query: data.query,
      page: data.page,
      page_size: data.page_size,
      total_rows: data.total_rows,
      total_pages: data.total_pages,
      my_rank: data.my_rank,
      truncated: data.truncated,
      rows: data.rows,
    }
  } catch (error) {
    handleApiFailure(error, 'Не удалось загрузить лидерборд.')
  }
}
