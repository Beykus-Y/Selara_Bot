export type FamilyNode = {
  id: number
  label: string
  role: string
  href: string
}

export type FamilyEdge = {
  source: number
  target: number
  label: string
  relation_type: string
  is_direct: boolean
}

export type FamilySummaryItem = {
  label: string
  value: string
}

export type FamilyPageData = {
  chat_id: number
  chat_title: string
  focus_user_id: number
  focus_label: string
  family_nodes: FamilyNode[]
  family_edges: FamilyEdge[]
  bundle_summary: FamilySummaryItem[]
}

export type FamilyPageSuccessResponse = {
  ok: true
  page: FamilyPageData
}

export type FamilyPageErrorResponse = {
  ok: false
  message: string
  redirect?: string
}

export type FamilyPageResponse = FamilyPageSuccessResponse | FamilyPageErrorResponse
