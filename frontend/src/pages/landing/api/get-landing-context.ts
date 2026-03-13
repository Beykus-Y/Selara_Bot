import { http } from '@/shared/api/http'
import { redirectToServerPath } from '@/shared/lib/resolve-server-redirect'
import type { LandingPageData, LandingPageResponse } from '@/pages/landing/model/types'

export async function getLandingContext(): Promise<LandingPageData> {
  const { data } = await http.get<LandingPageResponse>('/landing/context')

  if (!data.ok) {
    redirectToServerPath(data.redirect)
    throw new Error(data.message)
  }

  return data.page
}
