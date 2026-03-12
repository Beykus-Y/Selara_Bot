export type HomeMetric = {
  label: string
  value: string
  note: string
  tone: string
}

export type HomeGroupLink = {
  href: string
  title: string
  meta: string
  badge: string
}

export type HomeDashboardRow = {
  title: string
  meta: string
  value: string
}

export type HomeDashboardPanel = {
  title: string
  empty_text: string | null
  rows: HomeDashboardRow[]
}

export type HomeSecurityItem = {
  title: string
  text: string
}

export type HomePageData = {
  hero_title: string
  hero_subtitle: string
  metrics: HomeMetric[]
  admin_groups: HomeGroupLink[]
  activity_groups: HomeGroupLink[]
  global_dashboard: HomeDashboardPanel
  security_items: HomeSecurityItem[]
}

export type HomePageSuccessResponse = {
  ok: true
  page: HomePageData
}

export type HomePageErrorResponse = {
  ok: false
  message: string
  redirect?: string
}

export type HomePageResponse = HomePageSuccessResponse | HomePageErrorResponse
