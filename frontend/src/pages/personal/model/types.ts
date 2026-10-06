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

export type PersonalOverview = {
  checked_at: string
  subscription: PersonalSubscription
  quota: AiQuota
  profile: PersonalProfile
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
