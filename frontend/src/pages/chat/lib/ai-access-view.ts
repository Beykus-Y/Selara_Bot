import type { AiAutomaticSummaryState, AiQuota, ChatAiAccessData } from '@/pages/chat/model/ai-access'

export type QuotaView = {
  tone: 'ok' | 'warn' | 'muted'
  headline: string
  detail: string | null
  /** Share of the quota already used, 0-100; null when there is no bar to draw. */
  percentUsed: number | null
}

function formatInZone(value: string, timeZone: string, options: Intl.DateTimeFormatOptions) {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  try {
    return new Intl.DateTimeFormat('ru-RU', { ...options, timeZone }).format(date)
  } catch {
    return new Intl.DateTimeFormat('ru-RU', options).format(date)
  }
}

export function formatDate(value: string, timeZone: string) {
  return formatInZone(value, timeZone, { day: '2-digit', month: '2-digit', year: 'numeric' })
}

export function formatReset(value: string, timeZone: string) {
  return formatInZone(value, timeZone, { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' })
}

export function quotaView(quota: AiQuota, periodLabel: string, timeZone: string): QuotaView {
  if (quota.status === 'unavailable') {
    return { tone: 'muted', headline: 'Не удалось проверить', detail: null, percentUsed: null }
  }
  if (quota.status === 'unlimited' || quota.limit === null) {
    return { tone: 'ok', headline: 'Без коммерческого лимита', detail: null, percentUsed: null }
  }
  const used = quota.used ?? 0
  const remaining = quota.remaining ?? Math.max(0, quota.limit - used)
  const reset = quota.reset_at ? `Обновится ${formatReset(quota.reset_at, timeZone)}` : null
  const percentUsed = Math.min(100, Math.max(0, Math.round((used / quota.limit) * 100)))
  if (remaining <= 0) {
    return {
      tone: 'warn',
      headline: `Лимит исчерпан: ${used} из ${quota.limit} ${periodLabel}`,
      detail: reset,
      percentUsed: 100,
    }
  }
  return {
    tone: 'ok',
    headline: `Осталось ${remaining} из ${quota.limit}`,
    detail: [`Использовано ${used} ${periodLabel}`, reset].filter(Boolean).join(' · '),
    percentUsed,
  }
}

export const automaticSummaryLabels: Record<AiAutomaticSummaryState, string> = {
  active: 'Активны',
  available_disabled: 'Доступны, но выключены в настройках',
  requires_access_enabled: 'Включены в настройках, но требуют Selara AI',
  requires_access: 'Недоступны',
  provider_unavailable: 'Включены, но AI-провайдер сейчас недоступен',
  unknown: 'Статус временно не удалось проверить',
}

export type TierView = {
  badge: string
  tone: 'ok' | 'warn' | 'muted'
  note: string | null
}

export function tierView(data: ChatAiAccessData): TierView {
  if (data.state === 'unavailable' || data.tier === null) {
    return { badge: 'Не удалось проверить', tone: 'muted', note: 'Статус временно не удалось проверить.' }
  }
  if (data.tier === 'owner_internal') {
    return { badge: 'Внутренний доступ', tone: 'ok', note: null }
  }
  if (data.tier === 'paid') {
    const until = data.entitlement?.valid_until
    const note = until ? `Активна до ${formatDate(until, data.timezone)}` : 'Активна'
    return {
      badge: data.entitlement?.expiring_soon ? 'Скоро закончится' : 'Активна',
      tone: data.entitlement?.expiring_soon ? 'warn' : 'ok',
      note,
    }
  }
  const expired = data.entitlement?.expired_at
  return {
    badge: 'Бесплатный доступ',
    tone: 'muted',
    note: expired ? `Selara AI закончилась ${formatDate(expired, data.timezone)}` : null,
  }
}

export type PurchaseView = { label: string } | { hint: string } | null

export function purchaseView(data: ChatAiAccessData): PurchaseView {
  if (data.state === 'unavailable' || data.tier === 'owner_internal') return null
  if (!data.can_manage_purchase) {
    return { hint: 'Купить или продлить Selara AI может администратор группы.' }
  }
  if (!data.checkout_configured) return { hint: 'Покупка Selara AI пока недоступна.' }
  const renew = data.tier === 'paid' || Boolean(data.entitlement?.expired_at)
  return { label: renew ? 'Продлить' : 'Получить Selara AI' }
}
