import { http } from '@/shared/api/http'
import { redirectToServerPath } from '@/shared/lib/resolve-server-redirect'

type LoginContextSuccessResponse = {
  ok: true
  bot_username: string
  bot_dm_url: string
}

type LoginContextErrorResponse = {
  ok: false
  message: string
  redirect?: string
}

type LoginContextResponse = LoginContextSuccessResponse | LoginContextErrorResponse

export type LoginContextData = {
  bot_username: string
  bot_dm_url: string
}

export async function getLoginContext(): Promise<LoginContextData> {
  const { data } = await http.get<LoginContextResponse>('/login/context')

  if (!data.ok) {
    redirectToServerPath(data.redirect)
    throw new Error(data.message)
  }

  return {
    bot_username: data.bot_username,
    bot_dm_url: data.bot_dm_url,
  }
}
