export type AppViewer = {
  telegram_user_id: number
  display_name: string
  username: string
  first_name: string
  last_name: string
  initials: string
  avatar_url: string
}

export type AppViewerSuccessResponse = {
  ok: true
  viewer: AppViewer
}

export type AppViewerErrorResponse = {
  ok: false
  message: string
  redirect?: string
}

export type AppViewerResponse = AppViewerSuccessResponse | AppViewerErrorResponse
