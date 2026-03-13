import type { ChatSectionLink } from '@/pages/chat/model/types'

export type EconomyDashboardSummary = {
  balance: number
  growth_size_mm: number
  growth_actions: number
  farm_level: number
  farm_size_tier: string
}

export type EconomyPlotCard = {
  plot_no: number
  state: string
  crop_code: string | null
  crop_label: string
  note: string
}

export type EconomyInventoryItem = {
  item_code: string
  label: string
  quantity: number
  target: string
}

export type EconomyMarketRow = {
  id: number
  label: string
  item_code: string
  qty_left: number
  qty_total: number
  unit_price: number
  seller_label: string
  filter_group: string
  is_own: boolean
}

export type EconomyTradePoint = {
  when: string
  quantity: number
  unit_price: number
  total_price: number
}

export type EconomyTradePoints = Record<string, EconomyTradePoint[]>

export type EconomyPageData = {
  chat_id: number
  chat_title: string
  scope_id: string
  economy_mode: string
  dashboard: EconomyDashboardSummary
  plot_cards: EconomyPlotCard[]
  inventory_items: EconomyInventoryItem[]
  market_rows: EconomyMarketRow[]
  trade_points: EconomyTradePoints
  last_crop_label: string
  chat_section_links: ChatSectionLink[]
}

export type EconomyPageSuccessResponse = {
  ok: true
  page: EconomyPageData
}

export type EconomyPageErrorResponse = {
  ok: false
  message: string
  redirect?: string
}

export type EconomyPageResponse = EconomyPageSuccessResponse | EconomyPageErrorResponse
