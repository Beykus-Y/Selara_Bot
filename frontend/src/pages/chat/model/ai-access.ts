export type AiQuotaStatus = 'ok' | 'unlimited' | 'unavailable'

export type AiQuota = {
  status: AiQuotaStatus
  used: number | null
  limit: number | null
  remaining: number | null
  reset_at: string | null
  exhausted?: boolean
  /** What used/limit/remaining count: requests or AI Limits (Personal in AIL mode). */
  unit?: 'request' | 'ail'
}

export type AiAutomaticSummaryState =
  | 'active'
  | 'available_disabled'
  | 'requires_access_enabled'
  | 'requires_access'
  | 'provider_unavailable'
  | 'unknown'

export type ChatAiAccessData = {
  chat_id: number
  state: 'available' | 'unavailable'
  tier: 'free' | 'paid' | 'owner_internal' | null
  checked_at: string
  timezone: string
  can_manage_purchase: boolean
  checkout_configured: boolean
  provider_available?: boolean
  purchase: { command: string; bot_dm_url: string }
  entitlement: {
    active: boolean
    valid_until: string | null
    expired_at: string | null
    expiring_soon: boolean
    days_left: number | null
    source: string | null
  } | null
  llm: AiQuota
  manual_summary: AiQuota
  automatic_summary: {
    enabled: boolean
    access_allowed: boolean | null
    state: AiAutomaticSummaryState
  }
}
