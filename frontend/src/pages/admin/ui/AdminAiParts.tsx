import type { ReactNode } from 'react'

export function SectionRetry({ onRetry, busy }: { onRetry: () => void; busy: boolean }) {
  return (
    <button className="admin-retry" type="button" onClick={onRetry} disabled={busy}>
      {busy ? 'Проверяю…' : 'Повторить'}
    </button>
  )
}

export function SectionSkeleton({ rows = 2, label = 'Загрузка данных' }: { rows?: number; label?: string }) {
  return (
    <div className="admin-skeleton-list" aria-label={label} role="status">
      {Array.from({ length: rows }, (_, index) => <i key={index} />)}
    </div>
  )
}

export function SectionError({ message, onRetry, busy }: { message: string; onRetry: () => void; busy: boolean }) {
  return (
    <div className="admin-inline-error" role="alert">
      <span>{message}</span>
      <SectionRetry onRetry={onRetry} busy={busy} />
    </div>
  )
}

export function Metric({ label, value, note, tone }: {
  label: string
  value: ReactNode
  note?: ReactNode
  tone?: 'warn'
}) {
  return (
    <article className={tone === 'warn' ? 'admin-metric is-warn' : 'admin-metric'}>
      <strong>{value}</strong>
      <span>{label}</span>
      {note ? <small>{note}</small> : null}
    </article>
  )
}

export type BarPoint = { key: string; label: string; value: number }

/** Dependency-free bar chart; a text summary keeps the data readable without colour or sight. */
export function MiniBars({ points, title, format }: {
  points: BarPoint[]
  title: string
  format: (value: number) => string
}) {
  const max = Math.max(...points.map((point) => point.value), 0)
  const total = points.reduce((sum, point) => sum + point.value, 0)
  return (
    <figure className="admin-bars">
      <figcaption>{title}</figcaption>
      <div
        className="admin-bars__plot"
        role="img"
        aria-label={`${title}: ${points.length} дн., всего ${format(total)}, максимум ${format(max)}`}
      >
        {points.map((point) => (
          <span
            key={point.key}
            className="admin-bars__bar"
            title={`${point.label}: ${format(point.value)}`}
            style={{ height: `${max > 0 ? Math.max(4, Math.round((point.value / max) * 100)) : 4}%` }}
          />
        ))}
      </div>
      <div className="admin-bars__axis" aria-hidden="true">
        <span>{points[0]?.label}</span>
        <span>{points.at(-1)?.label}</span>
      </div>
    </figure>
  )
}
