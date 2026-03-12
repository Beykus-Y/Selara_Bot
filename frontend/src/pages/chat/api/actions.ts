import { postForm } from '@/shared/api/post-form'
import { handleApiFailure } from '@/shared/lib/api-response'
import { redirectToServerPath } from '@/shared/lib/resolve-server-redirect'

type ChatActionResponse = {
  ok: boolean
  message: string
  redirect?: string
}

async function submitChatAction(
  url: string,
  values: Record<string, string | number | boolean | null | undefined>,
  fallbackMessage: string,
) {
  try {
    const data = await postForm<ChatActionResponse>(url, values)

    if (!data.ok) {
      redirectToServerPath(data.redirect)
      throw new Error(data.message)
    }

    return data.message
  } catch (error) {
    handleApiFailure(error, fallbackMessage)
  }
}

export function saveChatAlias(
  chatId: string,
  values: { alias_text: string; source_trigger: string },
) {
  return submitChatAction(
    `/backend/app/chat/${chatId}/aliases`,
    values,
    'Не удалось сохранить алиас.',
  )
}

export function deleteChatAlias(chatId: string, aliasText: string) {
  return submitChatAction(
    `/backend/app/chat/${chatId}/aliases`,
    {
      action: 'delete',
      alias_text: aliasText,
    },
    'Не удалось удалить алиас.',
  )
}

export function saveChatTrigger(
  chatId: string,
  values: {
    trigger_id?: string
    keyword: string
    match_type: string
    response_text: string
    media_file_id: string
    media_type: string
  },
) {
  return submitChatAction(
    `/backend/app/chat/${chatId}/triggers`,
    values,
    'Не удалось сохранить триггер.',
  )
}

export function deleteChatTrigger(chatId: string, triggerId: string) {
  return submitChatAction(
    `/backend/app/chat/${chatId}/triggers`,
    {
      action: 'delete',
      trigger_id: triggerId,
    },
    'Не удалось удалить триггер.',
  )
}
