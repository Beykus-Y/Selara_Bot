export type AuditRow = {
  when: string
  action: string
  description: string
  actor: string
  target: string
}

export type AuditPageData = {
  chat_id: number
  chat_title: string
  audit_rows: AuditRow[]
}

export type AuditPageSuccessResponse = {
  ok: true
  page: AuditPageData
}

export type AuditPageErrorResponse = {
  ok: false
  message: string
  redirect?: string
}

export type AuditPageResponse = AuditPageSuccessResponse | AuditPageErrorResponse
