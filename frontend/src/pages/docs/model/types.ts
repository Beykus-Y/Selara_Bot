export type UserDocsItem = {
  title: string
  text: string
  badges?: string[]
  commands?: string[]
  triggers?: string[]
  examples?: string[]
  steps?: string[]
  notes?: string[]
}

export type UserDocsSection = {
  anchor: string
  title: string
  summary: string
  items: UserDocsItem[]
}

export type UserDocsOriginChat = {
  href: string
  label: string
} | null

export type UserDocsPageData = {
  hero_title: string
  hero_subtitle: string
  hero_chips: string[]
  docs_sections: UserDocsSection[]
  origin_chat: UserDocsOriginChat
}

export type UserDocsPageSuccessResponse = {
  ok: true
  page: UserDocsPageData
}

export type UserDocsPageErrorResponse = {
  ok: false
  message: string
  redirect?: string
}

export type UserDocsPageResponse = UserDocsPageSuccessResponse | UserDocsPageErrorResponse

export type AdminDocsFeatureItem = {
  title: string
  text: string
}

export type AdminDocsFeatureSection = {
  anchor: string
  title: string
  summary: string
  items: AdminDocsFeatureItem[]
}

export type AdminTriggerMatchType = {
  code: string
  label: string
  description: string
}

export type AdminTriggerTemplateVariable = {
  token: string
  label: string
  description: string
  availability: string
  aliases: string
}

export type AdminTriggerTemplateVariableGroup = {
  title: string
  items: AdminTriggerTemplateVariable[]
}

export type AdminSettingDocItem = {
  anchor: string
  key: string
  title: string
  description: string
  value_hint: string
}

export type AdminSettingDocSection = {
  anchor: string
  title: string
  items: AdminSettingDocItem[]
}

export type AdminDocsPageData = {
  hero_title: string
  hero_subtitle: string
  docs_sections: AdminDocsFeatureSection[]
  trigger_match_types: AdminTriggerMatchType[]
  trigger_template_variable_groups: AdminTriggerTemplateVariableGroup[]
  settings_docs_sections: AdminSettingDocSection[]
  origin_chat: UserDocsOriginChat
}

export type AdminDocsPageSuccessResponse = {
  ok: true
  page: AdminDocsPageData
}

export type AdminDocsPageErrorResponse = {
  ok: false
  message: string
  redirect?: string
}

export type AdminDocsPageResponse = AdminDocsPageSuccessResponse | AdminDocsPageErrorResponse
