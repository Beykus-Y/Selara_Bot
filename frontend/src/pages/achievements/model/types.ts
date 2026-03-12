export type AchievementMetric = {
  label: string
  value: string
  note: string
  tone: string
}

export type AchievementRow = {
  title: string
  meta: string
  value: string
  description: string
}

export type AchievementSection = {
  title: string
  rows: AchievementRow[]
}

export type AchievementsPageData = {
  hero_title: string
  hero_subtitle: string
  achievement_metrics: AchievementMetric[]
  achievement_sections: AchievementSection[]
}

export type AchievementsPageSuccessResponse = {
  ok: true
  page: AchievementsPageData
}

export type AchievementsPageErrorResponse = {
  ok: false
  message: string
  redirect?: string
}

export type AchievementsPageResponse = AchievementsPageSuccessResponse | AchievementsPageErrorResponse
