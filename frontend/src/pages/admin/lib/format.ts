export function formatCount(value: number) {
  return new Intl.NumberFormat('ru-RU').format(value)
}

export function formatStars(value: number) {
  return `${formatCount(value)} ⭐`
}

/** Known USD cost from a decimal string; tiny non-zero values never collapse to $0.00. */
export function formatUsd(value: string | null | undefined) {
  if (value === null || value === undefined) return '—'
  const amount = Number(value)
  if (!Number.isFinite(amount)) return '—'
  if (amount === 0) return '$0.00'
  const abs = Math.abs(amount)
  const digits = abs >= 1 ? 2 : abs >= 0.01 ? 4 : 6
  const formatted = amount.toLocaleString('en-US', { minimumFractionDigits: digits, maximumFractionDigits: digits })
  return Number(formatted.replace(/,/g, '')) === 0 ? `<$0.${'0'.repeat(digits - 1)}1` : `$${formatted}`
}

export function formatDateTime(value: string | null | undefined) {
  if (!value) return '—'
  const date = new Date(value)
  return Number.isNaN(date.getTime())
    ? value
    : new Intl.DateTimeFormat('ru-RU', { dateStyle: 'medium', timeStyle: 'short' }).format(date)
}

export function formatDay(value: string) {
  const date = new Date(`${value}T00:00:00`)
  return Number.isNaN(date.getTime())
    ? value
    : new Intl.DateTimeFormat('ru-RU', { day: '2-digit', month: '2-digit' }).format(date)
}

export const featureLabels: Record<string, string> = {
  llm_admin: '? / ??',
  daily_summary: 'Ежедневные итоги',
  autoconfig: 'Автонастройка',
  llm_context_compression: 'Сжатие контекста',
}

export function featureLabel(feature: string) {
  return featureLabels[feature] ?? feature
}

export const refundLabels: Record<string, string> = {
  pending: 'Возврат: в процессе',
  refunded: 'Возврат выполнен',
  failed: 'Возврат не удался',
}
