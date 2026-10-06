import type { PersonalOverview, PersonalQuota } from '@/pages/personal/model/types'

export type SubscriptionView = {
  badge: string
  tone: 'ok' | 'warn' | 'muted'
  note: string | null
}

function formatDay(value: string) {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return new Intl.DateTimeFormat('ru-RU', { day: '2-digit', month: '2-digit', year: 'numeric' }).format(date)
}

export function subscriptionView(subscription: PersonalOverview['subscription']): SubscriptionView {
  if (!subscription.available || subscription.tier === null) {
    return { badge: 'Статус не проверен', tone: 'muted', note: 'Не удалось проверить подписку. Это не значит, что доступ отключён.' }
  }
  if (subscription.tier === 'owner') {
    return { badge: 'Внутренний доступ', tone: 'ok', note: null }
  }
  if (subscription.tier === 'paid') {
    return {
      badge: subscription.valid_until ? `Selara Personal до ${formatDay(subscription.valid_until)}` : 'Selara Personal',
      tone: 'ok',
      note: null,
    }
  }
  return { badge: 'Бесплатный доступ', tone: 'warn', note: null }
}

export function quotaLine(quota: PersonalQuota): string | null {
  if (quota.unlimited || quota.limit === null) return 'Без коммерческого лимита'
  const used = quota.used ?? 0
  const remaining = quota.remaining ?? Math.max(0, quota.limit - used)
  if (remaining <= 0) return `Лимит на сегодня исчерпан: ${used} из ${quota.limit}`
  return `Сегодня осталось ${remaining} из ${quota.limit} запросов`
}

export function memoryCounter(count: number, limit: number | null): string {
  return limit === null ? String(count) : `${count} из ${limit}`
}

export function memoryExport(items: { content: string }[]): string {
  return items.map((item, index) => `${index + 1}. ${item.content}`).join('\n')
}
