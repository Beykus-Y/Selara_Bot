import type {
  ChatDailyActivityPoint,
  ChatDashboardPanel,
  ChatHeroOfDay,
  ChatLeaderboardSection,
  ChatMetric,
  ChatOverviewSummary,
  ChatRichestOfDay,
} from '@/pages/chat/model/types'
import type { GamesCard, GamesMetric, GamesRecentCard } from '@/pages/games/model/types'
import type { HomeDashboardPanel, HomeMetric } from '@/pages/home/model/types'
import type { AppViewer } from '@/widgets/app-shell/model/types'

export type MiniAppGroup = {
  chat_id: number
  title: string
  meta: string
  badge: string
  is_admin: boolean
  last_seen_at: string
  message_count: number
}

export type MiniAppRecentGameSummary = {
  game_id: string
  chat_id: number
  chat_title: string
  title: string
  kind: string
  started_at: string
  result_text: string
}

export type MiniAppHomePageData = {
  hero_title: string
  hero_subtitle: string
  metrics: HomeMetric[]
  recent_groups: MiniAppGroup[]
  admin_groups: MiniAppGroup[]
  recent_games: MiniAppRecentGameSummary[]
  global_dashboard: HomeDashboardPanel
  desktop_url: string
}

export type MiniAppGroupsPageData = {
  hero_title: string
  hero_subtitle: string
  admin_groups: MiniAppGroup[]
  activity_groups: MiniAppGroup[]
  desktop_url: string
}

export type MiniAppChatPageData = {
  chat_id: number
  chat_title: string
  hero_subtitle: string
  metrics: ChatMetric[]
  summary: ChatOverviewSummary
  daily_activity: ChatDailyActivityPoint[]
  hero_of_day: ChatHeroOfDay
  richest_of_day: ChatRichestOfDay
  dashboard_panels: ChatDashboardPanel[]
  leaderboards: ChatLeaderboardSection[]
  desktop_url: string
}

export type MiniAppGamesPageData = {
  hero_title: string
  hero_subtitle: string
  metrics: GamesMetric[]
  game_cards: GamesCard[]
  recent_game_cards: GamesRecentCard[]
  game_catalog: []
  spy_category_options: []
  whoami_category_options: []
  zlob_category_options: []
  default_create_kind: string
  default_create_game: null
  create_chat_options: []
  busy_create_chat_options: []
  has_manageable_chats: boolean
  desktop_url: string
}

export type MiniAppSessionData = {
  viewer: AppViewer
  miniapp_url: string
}

export type MiniAppContextValue = {
  viewer: AppViewer
  miniappUrl: string
  logout: () => Promise<void>
  reopen: () => void
}
