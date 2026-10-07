import type { AiQuota } from '@/pages/chat/model/ai-access'

export type PersonalMemoryItem = {
  id: number
  content: string
  pinned: boolean
  source: string
  created_at: string | null
}

export type PersonalSubscription = {
  state: 'available' | 'unavailable'
  tier: 'free' | 'paid' | 'owner_internal' | null
  active: boolean
  owner_exempt: boolean
  valid_until: string | null
  days_left: number | null
  expiring_soon: boolean
  offer_available: boolean
  price_stars: number | null
  duration_days: number
  purchase: { command: string; bot_dm_url: string }
}

export type PersonalProfile = {
  memory_enabled: boolean
  auto_memory_enabled: boolean
  auto_memory_available: boolean
  display_name: string | null
  mode: 'assistant' | 'roleplay'
}

export type PersonalModelOption = {
  profile_key: string
  emoji: string
  display_name: string
  description: string
  /** AIL per request, printed without trailing zeros ("2.5"). */
  ail_multiplier: string
  available: boolean
}

export type PersonalModel = {
  quota_mode: 'requests' | 'ail'
  /** Profiles can be chosen only while Personal counts AI Limits. */
  selectable: boolean
  selected: string
  effective: string
  effective_name: string
  fell_back: boolean
  /** Reserve a request needs to start; with billing "actual" the charge is the real cost of the answer. */
  cost_ail: string
  /** "actual": AIL are settled at the real cost of each request; "fixed": the profile multiplier; null: requests mode. */
  billing: 'actual' | 'fixed' | null
  /** AIL the latest settled request cost ("0.43"), null before the first one. */
  last_charge_ail: string | null
  options: PersonalModelOption[]
}

export type PersonalOverview = {
  checked_at: string
  /** Zone of the daily quota boundary (BOT_TIMEZONE); dates on the page are shown in it. */
  timezone: string
  subscription: PersonalSubscription
  quota: AiQuota
  profile: PersonalProfile
  model: PersonalModel
  memory: {
    count: number
    /** null while access could not be checked: writes are refused then. */
    limit: number | null
    items: PersonalMemoryItem[]
  }
}

export type PersonalSettingsPatch = Partial<Pick<PersonalProfile, 'memory_enabled' | 'auto_memory_enabled'>>

export type ForgetAllResult = {
  memories: number
  messages: number
  summaries: number
  profile: boolean
}
