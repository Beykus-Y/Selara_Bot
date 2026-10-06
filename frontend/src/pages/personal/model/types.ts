export type PersonalProfile = {
  display_name: string
  character_preset: string
  character_title: string
  character_custom: string | null
  address_form: string | null
  formality: 'ty' | 'vy'
  reply_length: 'short' | 'medium' | 'long'
  emoji_enabled: boolean
  mode: 'assistant' | 'roleplay'
  memory_enabled: boolean
  auto_memory_enabled: boolean
  revision: number
}

export type PersonalQuota = {
  limit: number | null
  used: number | null
  remaining: number | null
  reset_at: string | null
  unlimited: boolean
}

export type PersonalOverview = {
  profile: PersonalProfile
  options: {
    presets: { key: string; title: string }[]
    max_display_name: number
    max_custom_character: number
    max_address: number
  }
  subscription: {
    available: boolean
    tier: 'free' | 'paid' | 'owner' | null
    valid_until: string | null
    quota: PersonalQuota
    price_stars: number | null
    duration_days: number
    offer_available: boolean
    ai_available: boolean
    purchase_command: string
    bot_url: string
  }
  memory: {
    count: number
    limit: number | null
    max_length: number
    auto_extract_enabled_by_admin: boolean
    auto_extract_available: boolean
  }
}

export type PersonalMemory = {
  id: number
  content: string
  source: 'explicit' | 'extracted'
  pinned: boolean
  created_at: string | null
  last_used_at: string | null
}

export type PersonalMemoryList = {
  items: PersonalMemory[]
  count: number
  limit: number | null
  max_length: number
  memory_enabled: boolean
}

export type PersonalProfileChanges = Partial<
  Pick<
    PersonalProfile,
    | 'display_name'
    | 'character_preset'
    | 'character_custom'
    | 'address_form'
    | 'formality'
    | 'reply_length'
    | 'emoji_enabled'
    | 'mode'
    | 'memory_enabled'
    | 'auto_memory_enabled'
  >
>
