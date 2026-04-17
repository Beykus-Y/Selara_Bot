import { Link } from 'react-router-dom'

import type { HomeDashboardPanel, HomeMetric } from '@/pages/home/model/types'
import { routes } from '@/shared/config/routes'
import type { MiniAppGroup, MiniAppRecentGameSummary } from '@/shared/miniapp/model'

function filterMeta(meta: string): string {
  return meta
    .split(/\s*(?:•|·|\|)\s*/u)
    .map((t) => t.trim())
    .filter((t) => t && !/\bID\b/i.test(t) && !/-?\d{7,}/.test(t))
    .join(' · ')
}

export function MiniMetricGrid({ items }: { items: HomeMetric[] }) {
  return (
    <section className="miniapp-metric-grid">
      {items.map((item) => (
        <article key={`${item.label}-${item.tone}`} className={`miniapp-metric-card miniapp-metric-card--${item.tone}`}>
          <span>{item.label}</span>
          <strong>{item.value}</strong>
          <p>{item.note}</p>
        </article>
      ))}
    </section>
  )
}

export function MiniGroupSection({
  title,
  text,
  items,
  emptyText,
}: {
  title: string
  text: string
  items: MiniAppGroup[]
  emptyText: string
}) {
  return (
    <section className="miniapp-section-card">
      <div className="miniapp-section-head">
        <div>
          <h2>{title}</h2>
          <p>{text}</p>
        </div>
      </div>

      {items.length > 0 ? (
        <div className="miniapp-group-list">
          {items.map((group) => (
            <Link key={group.chat_id} className="miniapp-group-card" to={routes.chat(group.chat_id)}>
              <div className="miniapp-group-card__head">
                <strong>{group.title}</strong>
                <span className={`miniapp-badge miniapp-badge--${group.badge}`}>{group.is_admin ? 'Админ' : 'Участник'}</span>
              </div>
              <p>{filterMeta(group.meta)}</p>
            </Link>
          ))}
        </div>
      ) : (
        <div className="miniapp-empty-card">
          <strong>Пока пусто</strong>
          <p>{emptyText}</p>
        </div>
      )}
    </section>
  )
}

export function MiniRecentGamesSection({
  title,
  text,
  items,
  emptyText,
}: {
  title: string
  text: string
  items: MiniAppRecentGameSummary[]
  emptyText: string
}) {
  return (
    <section className="miniapp-section-card">
      <div className="miniapp-section-head">
        <div>
          <h2>{title}</h2>
          <p>{text}</p>
        </div>
      </div>

      {items.length > 0 ? (
        <div className="miniapp-list-stack">
          {items.map((game) => (
            <Link key={game.game_id} className="miniapp-inline-card" to={routes.games}>
              <div>
                <strong>{game.title}</strong>
                <p>{game.chat_title}</p>
              </div>
              <div className="miniapp-inline-card__meta">
                <span>{game.started_at}</span>
                <span>{game.result_text}</span>
              </div>
            </Link>
          ))}
        </div>
      ) : (
        <div className="miniapp-empty-card">
          <strong>Пока нет истории</strong>
          <p>{emptyText}</p>
        </div>
      )}
    </section>
  )
}

export function MiniDashboardPanel({ panel }: { panel: HomeDashboardPanel }) {
  return (
    <section className="miniapp-section-card">
      <div className="miniapp-section-head">
        <div>
          <h2>{panel.title}</h2>
          <p>{panel.empty_text || 'Ключевые значения текущего контура.'}</p>
        </div>
      </div>

      {panel.rows.length > 0 ? (
        <div className="miniapp-list-stack">
          {panel.rows.map((row) => (
            <article key={`${panel.title}-${row.title}`} className="miniapp-inline-card">
              <div>
                <strong>{row.title}</strong>
                <p>{row.meta}</p>
              </div>
              <div className="miniapp-inline-card__value">{row.value}</div>
            </article>
          ))}
        </div>
      ) : (
        <div className="miniapp-empty-card">
          <strong>Контур пока пуст</strong>
          <p>{panel.empty_text || 'Данные появятся после первых действий пользователя.'}</p>
        </div>
      )}
    </section>
  )
}
