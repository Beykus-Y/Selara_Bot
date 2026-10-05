import type { AxiosRequestConfig } from 'axios'

import type { ChatAiAccessData } from '@/pages/chat/model/ai-access'
import { getMiniAppData } from '@/shared/miniapp/api'

export function getChatAiAccess(chatId: string, config?: AxiosRequestConfig) {
  return getMiniAppData<ChatAiAccessData>(
    `/miniapp/chat/${chatId}/ai-access`,
    'Не удалось загрузить статус Selara AI.',
    config,
  )
}
