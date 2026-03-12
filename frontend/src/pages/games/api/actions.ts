import { postForm } from '@/shared/api/post-form'
import { handleApiFailure } from '@/shared/lib/api-response'
import { redirectToServerPath } from '@/shared/lib/resolve-server-redirect'

type ActionResponse = {
  ok: boolean
  message: string
  redirect?: string
}

async function submitGamesAction(
  url: string,
  values: Record<string, string | number | boolean | null | undefined>,
) {
  try {
    const data = await postForm<ActionResponse>(url, values)

    if (!data.ok) {
      redirectToServerPath(data.redirect)
      throw new Error(data.message)
    }

    return data.message
  } catch (error) {
    handleApiFailure(error, 'Не удалось выполнить игровое действие.')
  }
}

export function createGame(values: {
  kind: string
  chat_id: string
  spy_category?: string
  whoami_category?: string
  zlob_category?: string
}) {
  return submitGamesAction('/backend/app/games/create', values)
}

export function runGameAction(
  values: Record<string, string | number | boolean | null | undefined>,
) {
  return submitGamesAction('/backend/app/games/action', values)
}
