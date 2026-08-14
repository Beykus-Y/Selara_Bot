export type BroadcastDetail = {
  id: number
  created_at: string
  body: string
  message_preview_html: string
  active_since_days: number
  target_count: number
  sent_count: number
  failed_count: number
  reply_count: number
  reaction_count: number
  media_type: 'photo' | null
  reaction_options: Array<{ key: string; emoji: string; label: string }>
}

export type BroadcastDelivery = {
  chat_id: number
  chat_title: string
  last_activity_at: string
  status_code: 'pending' | 'sent' | 'failed'
  status_label: string
  status_tone: string
  telegram_message_id: number | null
  reply_count: number
  reaction_mode: 'none' | 'native' | 'inline'
  bot_member_status: string | null
  sent_at: string
  error_text: string | null
}

export type BroadcastReply = {
  chat_title: string
  user_label: string
  sent_at: string
  message_type: string
  preview: string
}

export type BroadcastReaction = {
  chat_title: string
  user_label: string
  source: 'native' | 'inline'
  option_key: string
  emoji: string
  reacted_at: string
}

export type BroadcastReactionCount = {
  chat_title: string
  emoji: string
  count: number
  observed_at: string
}

export type BroadcastPageData = {
  broadcast: BroadcastDetail
  deliveries: BroadcastDelivery[]
  replies: BroadcastReply[]
  reactions: BroadcastReaction[]
  anonymous_reaction_counts: BroadcastReactionCount[]
}

export type BroadcastPageSuccessResponse = {
  ok: true
  page: BroadcastPageData
}

export type BroadcastPageErrorResponse = {
  ok: false
  message: string
  redirect?: string
}

export type BroadcastPageResponse = BroadcastPageSuccessResponse | BroadcastPageErrorResponse
