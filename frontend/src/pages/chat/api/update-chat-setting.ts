import { handleApiFailure } from '@/shared/lib/api-response'
import { redirectToServerPath } from '@/shared/lib/resolve-server-redirect'
import { postForm } from '@/shared/api/post-form'

type UpdateChatSettingResponse = {
  ok: boolean
  message: string
  redirect?: string
  setting?: {
    key: string
    title: string
    current_value: string
    default_value: string
  }
}

export async function updateChatSetting(chatId: string, key: string, value: string) {
  try {
    const data = await postForm<UpdateChatSettingResponse>(`/backend/app/chat/${chatId}/settings`, {
      key,
      value,
    })

    if (!data.ok) {
      redirectToServerPath(data.redirect)
      throw new Error(data.message)
    }

    return data
  } catch (error) {
    handleApiFailure(error, 'Не удалось обновить настройку группы.')
  }
}
