import type { AxiosRequestConfig } from 'axios'

import { getMiniAppData } from '@/shared/miniapp/api'

/** USD and Stars values arrive as strings/integers so no float rounding happens on the server. */
export type AdminAiSummary = {
  period_days: number
  window_from: string
  window_to: string
  invocations: number
  provider_calls: number
  unsuccessful_invocations: number
  unknown_cost_calls: number
  known_cost_usd: string
  average_known_cost_per_invocation_usd: string | null
  daily: Array<{ date: string; invocations: number; provider_calls: number; known_cost_usd: string }>
}

export type AdminAiFeatureRow = {
  feature: string
  invocations: number
  unsuccessful_invocations: number
  provider_calls: number
  known_cost_usd: string
  unknown_cost_calls: number
}

export type AdminAiModelRow = {
  model: string
  provider_calls: number
  prompt_tokens: number
  completion_tokens: number
  known_cost_usd: string
  unknown_cost_calls: number
}

export type AdminAiBreakdown = {
  period_days: number
  features: AdminAiFeatureRow[]
  models: AdminAiModelRow[]
  unattributed_provider_calls: number
  stages: Array<{ feature: string; stage: string; provider_calls: number; known_cost_usd: string }>
}

export type AdminMonetizationSummary = {
  period_days: number
  currency: 'XTR'
  successful_payments: number
  stars_revenue: number
  rejected_payments: number
  refunds: { pending: number; refunded: number; failed: number }
  all_time: { successful_payments: number; stars_revenue: number }
  active_paid_chats: number
  expiring_within_7_days: number
  active_personal_subscriptions?: number
  personal_expiring_within_7_days?: number
  daily: Array<{ date: string; payments: number; stars: number }>
  checkout: { configured: boolean; price_stars: number | null }
}

export type AdminRefund = {
  status: 'pending' | 'refunded' | 'failed'
  requested_at: string | null
  completed_at: string | null
  result_code: string | null
}

export type AdminPayment = {
  id: number
  payment_at: string
  buyer_user_id: number
  source_chat_id: number | null
  target_chat_id: number | null
  target_scope?: 'chat' | 'user'
  target_user_id?: number | null
  chat_id: number | null
  chat_title: string | null
  amount_stars: number
  currency: string
  state: 'applied' | 'rejected'
  reason: string | null
  product_key: string | null
  refund: AdminRefund | null
}

export type AdminPaymentDetail = AdminPayment & {
  telegram_payment_charge_id: string
  refund_command: string | null
  intent: {
    id: string
    status: string
    created_at: string | null
    expires_at: string | null
    amount_stars: number
    source_chat_id: number | null
    chat_id: number | null
    terms_version: string | null
    terms_accepted_at: string | null
  } | null
  entitlement: {
    status: string
    valid_from: string | null
    valid_until: string | null
    active_now: boolean
  } | null
}

export type AdminPaymentFilters = {
  state: 'all' | 'applied' | 'rejected'
  refund: 'all' | 'none' | 'pending' | 'refunded' | 'failed'
  chatId: string
  buyerId: string
  periodDays: number | null
}

export type AdminPaymentsPage = { items: AdminPayment[]; next_cursor: string | null }

export type AdminEntitlements = {
  active_paid_chats: number
  expiring_within_7_days: number
  items: Array<{
    chat_id: number
    chat_title: string | null
    product_key: string
    valid_until: string
    days_left: number
    expiring_soon: boolean
    last_purchase_at: string | null
  }>
}

export type AdminReadiness = {
  checkout: { configured: boolean; price_stars: number | null; product_key: string }
  checks: Array<{ key: string; label: string; status: 'ok' | 'unavailable' | 'missing'; detail: string }>
}

export function getAdminAiSummary(periodDays: number, config?: AxiosRequestConfig) {
  return getMiniAppData<AdminAiSummary>('/miniapp/admin/ai/summary', 'Не удалось загрузить AI-аналитику.', {
    ...config,
    params: { period_days: periodDays },
  })
}

export function getAdminAiBreakdown(periodDays: number, config?: AxiosRequestConfig) {
  return getMiniAppData<AdminAiBreakdown>('/miniapp/admin/ai/breakdown', 'Не удалось загрузить разбивку расходов.', {
    ...config,
    params: { period_days: periodDays },
  })
}

export function getAdminMonetizationSummary(periodDays: number, config?: AxiosRequestConfig) {
  return getMiniAppData<AdminMonetizationSummary>(
    '/miniapp/admin/monetization/summary',
    'Не удалось загрузить аналитику Stars.',
    { ...config, params: { period_days: periodDays } },
  )
}

export function getAdminPayments(
  filters: AdminPaymentFilters,
  cursor: string | null,
  config?: AxiosRequestConfig,
) {
  return getMiniAppData<AdminPaymentsPage>('/miniapp/admin/monetization/payments', 'Не удалось загрузить историю платежей.', {
    ...config,
    params: {
      limit: 20,
      state: filters.state,
      refund: filters.refund,
      ...(filters.chatId ? { chat_id: filters.chatId } : {}),
      ...(filters.buyerId ? { buyer_id: filters.buyerId } : {}),
      ...(filters.periodDays ? { period_days: filters.periodDays } : {}),
      ...(cursor ? { cursor } : {}),
    },
  })
}

export function getAdminPaymentDetail(paymentId: number, config?: AxiosRequestConfig) {
  return getMiniAppData<AdminPaymentDetail>(
    `/miniapp/admin/monetization/payments/${paymentId}`,
    'Не удалось загрузить платёж.',
    config,
  )
}

export function getAdminEntitlements(config?: AxiosRequestConfig) {
  return getMiniAppData<AdminEntitlements>(
    '/miniapp/admin/monetization/entitlements',
    'Не удалось загрузить активные подписки.',
    config,
  )
}

export function getAdminAiReadiness(config?: AxiosRequestConfig) {
  return getMiniAppData<AdminReadiness>('/miniapp/admin/ai/readiness', 'Не удалось проверить готовность.', config)
}
