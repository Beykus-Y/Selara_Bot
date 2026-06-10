import { Link } from 'react-router-dom'

import type { HomeDashboardPanel, HomeMetric } from '@/pages/home/model/types'
import { routes } from '@/shared/config/routes'
import type { MiniAppGroup, MiniAppRecentGameSummary } from '@/shared/miniapp/model'

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
  items,
  emptyText,
}: {
  title: string
  text?: string
  items: MiniAppGroup[]
  emptyText: string
}) {
  const gradients = [
    'linear-gradient(140deg, #7c5cff, #4f3dbb)',
    'linear-gradient(140deg, #e86aa6, #a4426f)',
    'linear-gradient(140deg, #56a4f8, #3568b8)',
    'linear-gradient(140deg, #4ade80, #27895a)',
    'linear-gradient(140deg, #f3b94d, #b87d22)',
  ]

  return (
    <section>
      <h2 className="sec">{title}</h2>

      {items.length > 0 ? (
        <div>
          {items.map((group) => {
            const gradient = gradients[Math.abs(group.chat_id) % gradients.length]
            const firstLetter = group.title.trim().slice(0, 1).toUpperCase() || '?'
            
            let actClass = 'stale'
            const lastSeen = group.last_seen_at || 'давно'
            if (/минут|секунд|онлайн|online|сейчас/i.test(lastSeen)) {
              actClass = 'online'
            } else if (/час|вчера/i.test(lastSeen)) {
              actClass = 'recent'
            }

            return (
              <Link key={group.chat_id} className="group-row" to={routes.chat(group.chat_id)}>
                <div className="g-ava" style={{ background: gradient }}>
                  {firstLetter}
                </div>
                <div className="g-body">
                  <div className="g-name">
                    <b>{group.title}</b>
                    {(group.badge === 'owner' || group.is_admin) && (
                      <span className={`chip ${group.badge === 'owner' ? 'owner' : 'coowner'}`}>
                        {group.badge === 'owner' ? 'владелец' : 'совладелец'}
                      </span>
                    )}
                  </div>
                  <div className="g-meta">
                    <span className="mono">{group.message_count?.toLocaleString() ?? 0}</span> сообщений
                  </div>
                </div>
                <div className="g-right">
                  <div className={`g-act ${actClass}`}>{lastSeen}</div>
                  <div className="g-arrow">›</div>
                </div>
              </Link>
            )
          })}
        </div>
      ) : (
        <div className="card">
          <div style={{ color: 'var(--text-3)', fontSize: '13px', textAlign: 'center' }}>
            {emptyText}
          </div>
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
