import type { AxiosRequestConfig } from 'axios'

import type { SelaraAction, SelaraChatSettings } from '@/pages/chat/model/selara-character'
import { getMiniAppData, postMiniAppData } from '@/shared/miniapp/api'

export function getSelaraChatSettings(chatId: string, config?: AxiosRequestConfig) {
  return getMiniAppData<SelaraChatSettings>(
    `/miniapp/chat/${chatId}/selara`,
    'Не удалось загрузить настройки Selara.',
    config,
  )
}

export function updateSelaraChatSettings(chatId: string, action: SelaraAction, value = '') {
  return postMiniAppData<SelaraChatSettings & { message: string }>(
    `/miniapp/chat/${chatId}/selara`,
    { action, value },
    'Не удалось сохранить настройки Selara.',
  )
}
