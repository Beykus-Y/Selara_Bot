import type { FeedbackItem } from '@/pages/feedback/model/types'

export type ActiveChat = {
  chat_id: number
  title: string
  last_activity_at: string
  checked: boolean
}

export type RecentBroadcast = {
  id: number
  created_at: string
  body_preview: string
  target_count: number
  sent_count: number
  failed_count: number
  reply_count: number
}

export type TableEntry = [string, string]

export type TableSection = {
  title: string
  tables: TableEntry[]
}

export type AdminPageData = {
  admin_user_id: number
  open_feedback_count: number
  feedback_requests: FeedbackItem[]
  broadcast_active_days: number
  recent_active_chat_count: number
  recent_active_chats: ActiveChat[]
  recent_broadcasts: RecentBroadcast[]
  table_sections: TableSection[]
}

export type AdminPageSuccessResponse = {
  ok: true
  page: AdminPageData
}

export type AdminPageErrorResponse = {
  ok: false
  message: string
  redirect?: string
}

export type AdminPageResponse = AdminPageSuccessResponse | AdminPageErrorResponse

export type AdminActionResponse =
  | { ok: true; message: string; broadcast_id?: number }
  | { ok: false; message: string; redirect?: string }
