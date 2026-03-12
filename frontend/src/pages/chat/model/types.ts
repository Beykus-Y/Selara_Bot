export type ChatOverviewSummary = {
  participants_count: number
  total_messages: number
  last_activity_at: string
}

export type ChatMetric = {
  label: string
  value: string
  note: string
  tone: string
}

export type ChatInfoRow = {
  title: string
  meta: string
  value: string
}

export type ChatDashboardPanel = {
  title: string
  empty_text: string | null
  rows: ChatInfoRow[]
}

export type ChatRoleRow = {
  title: string
  code: string
  rank: string
  meta: string
  permissions: string
}

export type ChatCommandRule = {
  command: string
  role: string
}

export type ChatLeaderboardSectionRow = {
  position: string
  name: string
  primary: string
  secondary: string
}

export type ChatLeaderboardSection = {
  title: string
  subtitle: string
  accent: string
  rows: ChatLeaderboardSectionRow[]
}

export type ChatAuditRow = {
  when: string
  action: string
  description: string
  actor: string
  target: string
}

export type ChatDailyActivityPoint = {
  date: string
  label: string
  messages: number
}

export type ChatHeroOfDay = {
  label: string
  messages: number
  karma: number
} | null

export type ChatRichestOfDay = {
  label: string
  balance: number
} | null

export type ChatOverviewData = {
  chat_id: number
  chat_title: string
  hero_subtitle: string
  metrics: ChatMetric[]
  summary: ChatOverviewSummary
  daily_activity: ChatDailyActivityPoint[]
  hero_of_day: ChatHeroOfDay
  richest_of_day: ChatRichestOfDay
  dashboard_panels: ChatDashboardPanel[]
  access_rows: ChatInfoRow[]
  roles: ChatRoleRow[]
  command_rules: ChatCommandRule[]
  leaderboards: ChatLeaderboardSection[]
  audit_rows: ChatAuditRow[]
  can_manage_settings: boolean
}

export type ChatOverviewSuccessResponse = {
  ok: true
} & ChatOverviewData

export type ChatOverviewErrorResponse = {
  ok: false
  message: string
  redirect?: string
}

export type ChatOverviewResponse = ChatOverviewSuccessResponse | ChatOverviewErrorResponse

export type ChatLeaderboardRow = {
  position: number
  user_id: number
  name: string
  username: string
  activity: number
  karma: number
  hybrid_score: number
  last_seen_at: string
  is_me: boolean
}

export type ChatLeaderboardMode = 'mix' | 'activity' | 'karma'

export type ChatLeaderboardData = {
  mode: ChatLeaderboardMode
  query: string
  page: number
  page_size: number
  total_rows: number
  total_pages: number
  my_rank: number | null
  truncated: boolean
  rows: ChatLeaderboardRow[]
}

export type ChatLeaderboardSuccessResponse = {
  ok: true
} & ChatLeaderboardData

export type ChatLeaderboardErrorResponse = {
  ok: false
  message: string
  redirect?: string
}

export type ChatLeaderboardResponse = ChatLeaderboardSuccessResponse | ChatLeaderboardErrorResponse

export type ChatAchievementRow = {
  title: string
  meta: string
  value: string
  description: string
}

export type ChatAchievementSection = {
  title: string
  rows: ChatAchievementRow[]
}

export type ChatAchievementsData = {
  chat_id: number
  chat_title: string
  can_manage_settings: boolean
  achievement_sections: ChatAchievementSection[]
}

export type ChatAchievementsSuccessResponse = {
  ok: true
} & ChatAchievementsData

export type ChatAchievementsErrorResponse = {
  ok: false
  message: string
  redirect?: string
}

export type ChatAchievementsResponse = ChatAchievementsSuccessResponse | ChatAchievementsErrorResponse

export type ChatSettingOption = {
  value: string
  label: string
  selected: boolean
}

export type ChatAliasRow = {
  id: string
  alias: string
  command: string
  source: string
}

export type ChatTriggerRow = {
  id: string
  keyword: string
  match_type: string
  match_type_label: string
  preview: string
  response_text: string
  media_file_id: string
  media_type: string
}

export type ChatTriggerTemplateQuickRow = {
  token: string
  description: string
}

export type ChatSettingItem = {
  key: string
  title: string
  description: string
  hint: string
  current_value: string
  default_value: string
  editable: boolean
  input_kind: string
  options: ChatSettingOption[]
  doc_anchor: string
}

export type ChatSettingsSection = {
  title: string
  items: ChatSettingItem[]
}

export type ChatSettingsData = {
  chat_id: number
  chat_title: string
  can_manage_settings: boolean
  manage_settings_note: string
  manage_settings_tone: string
  admin_docs_url: string
  settings_sections: ChatSettingsSection[]
  aliases: ChatAliasRow[]
  triggers: ChatTriggerRow[]
  trigger_template_quick_rows: ChatTriggerTemplateQuickRow[]
  trigger_template_examples: string[]
  trigger_template_docs_url: string
  audit_rows: ChatAuditRow[]
}

export type ChatSettingsSuccessResponse = {
  ok: true
} & ChatSettingsData

export type ChatSettingsErrorResponse = {
  ok: false
  message: string
  redirect?: string
}

export type ChatSettingsResponse = ChatSettingsSuccessResponse | ChatSettingsErrorResponse
