export type FeedbackMetric = {
  label: string
  value: string
  note: string
  tone: string
}

export type FeedbackItem = {
  id: number
  title: string
  details: string
  status_code: 'open' | 'done'
  status_label: string
  status_note: string
  is_done: boolean
  created_at: string
  done_at: string | null
  author_label?: string | null
}

export type FeedbackPageData = {
  feedback_metrics: FeedbackMetric[]
  feedback_items: FeedbackItem[]
}

export type FeedbackPageSuccessResponse = {
  ok: true
  page: FeedbackPageData
}

export type FeedbackPageErrorResponse = {
  ok: false
  message: string
  redirect?: string
}

export type FeedbackPageResponse = FeedbackPageSuccessResponse | FeedbackPageErrorResponse

export type FeedbackSubmitResponse =
  | { ok: true; message: string; request_id?: number }
  | { ok: false; message: string; redirect?: string }
