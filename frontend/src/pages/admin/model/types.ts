export type AdminHealthStatus = 'healthy' | 'down' | 'unknown' | 'degraded'

export type AdminHealthComponent = {
  status: AdminHealthStatus
  latency_ms: number | null
  checked_at: string
  last_success_at: string | null
  detail?: string
}

export type AdminHealthData = {
  status: AdminHealthStatus
  environment: string
  version: string | null
  checked_at: string
  process_started_at: string
  process_uptime_seconds: number
  components: {
    web: AdminHealthComponent
    telegram_bot: AdminHealthComponent
    postgresql: AdminHealthComponent
    redis: AdminHealthComponent
    gacha_genshin: AdminHealthComponent
    gacha_hsr: AdminHealthComponent
  }
}

export type AdminAudienceData = {
  period_days: number
  generated_at: string
  metrics: {
    active_bot_users: { value: number; change_percent: number | null }
    total_bot_users: { value: number }
    active_group_users: { value: number; change_percent: number | null }
    total_group_members: {
      value: number | null
      status: 'available' | 'partial' | 'unavailable'
      checked_groups: number
      total_groups: number
      known_active_members: number
      note: string
    }
  }
}
