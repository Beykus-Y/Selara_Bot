import axios from 'axios'

import { resolveAppPath } from '@/shared/config/app-base-path'

type LogoutResponse = {
  ok: boolean
  message: string
  redirect?: string
}

export async function logoutSession(): Promise<LogoutResponse> {
  const response = await axios.post<LogoutResponse>(
    resolveAppPath('/logout'),
    undefined,
    {
      withCredentials: true,
      validateStatus: () => true,
      headers: {
        Accept: 'application/json',
        'X-Requested-With': 'fetch',
      },
    },
  )

  return response.data
}
