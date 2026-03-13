import axios from 'axios'

import { resolveAppPath } from '@/shared/config/app-base-path'

export type LoginSubmitResponse = {
  ok: boolean
  message: string
  redirect?: string
}

export async function submitLogin(code: string): Promise<LoginSubmitResponse> {
  const payload = new URLSearchParams()
  payload.set('code', code)

  const response = await axios.post<LoginSubmitResponse>(resolveAppPath('/backend/login'), payload.toString(), {
    withCredentials: true,
    validateStatus: () => true,
    headers: {
      Accept: 'application/json',
      'Content-Type': 'application/x-www-form-urlencoded',
      'X-Requested-With': 'fetch',
    },
  })

  return response.data
}
