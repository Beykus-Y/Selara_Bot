export type TableRow = {
  row: Record<string, unknown>
  pk_query: string
  pk_values: Record<string, unknown>
}

export type AdminTablePageData = {
  table_name: string
  table_title: string
  columns: string[]
  pk_columns: string[]
  rows: TableRow[]
  total: number
  page: number
  limit: number
  filters_input: Record<string, string>
  reference_labels: Record<string, Record<string, string>>
}

export type AdminTablePageSuccessResponse = {
  ok: true
  page: AdminTablePageData
}

export type AdminTablePageErrorResponse = {
  ok: false
  message: string
  redirect?: string
}

export type AdminTablePageResponse = AdminTablePageSuccessResponse | AdminTablePageErrorResponse

export type ColDef = {
  name: string
  value: unknown
  type: 'text' | 'textarea' | 'datetime' | 'bool'
  is_pk: boolean
  ref_label?: string | null
}

export type AdminTableEditPageData = {
  table_name: string
  table_title: string
  record_id: string
  pk_fields: [string, unknown][]
  pk_columns: string[]
  columns: ColDef[]
}

export type AdminTableEditPageSuccessResponse = {
  ok: true
  page: AdminTableEditPageData
}

export type AdminTableEditPageErrorResponse = {
  ok: false
  message: string
  redirect?: string
}

export type AdminTableEditPageResponse = AdminTableEditPageSuccessResponse | AdminTableEditPageErrorResponse

export type AdminTableActionResponse =
  | { ok: true; message: string }
  | { ok: false; message: string; redirect?: string }
