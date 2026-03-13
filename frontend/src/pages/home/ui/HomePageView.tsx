import { Link } from 'react-router-dom'

import type { HomeGroupLink, HomePageData } from '@/pages/home/model/types'
import { routes } from '@/shared/config/routes'
import { PanelGlyph } from '@/shared/ui/PanelGlyph'

import './home-page.css'

type HomePageViewProps = {
  data: HomePageData
}

type GroupMetaToken = {
  value: string
  dimmed?: boolean
}

function badgeLabel(value: string) {
  if (value === 'admin') {
    return 'Админ'
  }

  return 'Обычное'
}

function splitGroupTitle(title: string) {
  const cleanTitle = title.trim()
  const match = cleanTitle.match(/^(\S+)\s+(.+)$/u)

  if (!match) {
    return { emoji: null, label: cleanTitle }
  }

  const [, lead, label] = match

  if (!/[\p{Extended_Pictographic}]/u.test(lead)) {
    return { emoji: null, label: cleanTitle }
  }

  return { emoji: lead, label }
}

function splitGroupMeta(meta: string): GroupMetaToken[] {
  const cleanMeta = meta.trim()

  if (!cleanMeta) {
    return []
  }

  return cleanMeta
    .split(/\s*(?:•|·|\|)\s*/u)
    .map((token) => token.trim())
    .filter(Boolean)
    .map((value) => ({
      value,
      dimmed: /\bID\b/i.test(value) || /-?\d{7,}/.test(value),
    }))
}

function GroupSection({ title, groups }: { title: string; groups: HomeGroupLink[] }) {
  return (
    <article className="home-panel">
      <div className="home-panel__head">
        <h2>{title}</h2>
        <span className="home-panel__tag">{groups.length}</span>
      </div>

      {groups.length > 0 ? (
        <div className="home-list">
          {groups.map((group) => {
            const titleParts = splitGroupTitle(group.title)
            const metaTokens = splitGroupMeta(group.meta)

            return (
              <Link key={group.href} className="home-list-card" to={group.href}>
                <div className="home-list-card__title">
                  {titleParts.emoji ? <span className="home-list-card__emoji">{titleParts.emoji}</span> : null}

                  <div className="home-list-card__copy">
                    <strong>{titleParts.label}</strong>

                    {metaTokens.length > 0 ? (
                      <div className="home-list-card__meta">
                        {metaTokens.map((token) => (
                          <span
                            key={`${group.href}-${token.value}`}
                            className={token.dimmed ? 'home-list-card__meta-item home-list-card__meta-item--dim' : 'home-list-card__meta-item'}
                          >
                            {token.value}
                          </span>
                        ))}
                      </div>
                    ) : null}
                  </div>
                </div>

                <span className={`home-pill home-pill--${group.badge}`}>{badgeLabel(group.badge)}</span>
              </Link>
            )
          })}
        </div>
      ) : (
        <p className="home-empty">Пока нет данных для этого блока.</p>
      )}
    </article>
  )
}

export function HomePageView({ data }: HomePageViewProps) {
  const spotlightMetrics = data.metrics.slice(0, 2)
  const quickActions = [
    {
      title: 'Игровой центр',
      text: 'Лобби, live-сцены и архив последних партий в одном контуре.',
      label: 'Открыть игры',
      href: routes.games,
      icon: 'gamepad' as const,
    },
    {
      title: 'Глобальные достижения',
      text: 'Каталог аккаунта, редкость ачивок и статус получения.',
      label: 'К достижениям',
      href: routes.achievements,
      icon: 'trophy' as const,
    },
    {
      title: 'Настройки панели',
      text: 'Профиль, текущая сессия браузера и быстрые переходы.',
      label: 'Открыть настройки',
      href: routes.settings,
      icon: 'settings' as const,
    },
    {
      title: 'Справка пользователя',
      text: 'Команды, игровые сценарии и основные механики Selara.',
      label: 'Читать справку',
      href: routes.appDocs('user'),
      icon: 'docs' as const,
    },
  ]

  return (
    <div className="home-page">
      <section className="home-hero">
        <div className="home-hero__main">
          <div className="home-hero__copy">
            <span className="page-card__eyebrow">Личный кабинет</span>
            <h1>{data.hero_title}</h1>
            <p>{data.hero_subtitle}</p>
          </div>

          <div className="home-hero__chips">
            <span className="home-chip">Web + Telegram</span>
            <span className="home-chip">Одноразовый код</span>
            <span className="home-chip">Сводка по группам</span>
          </div>
        </div>

        <div className="home-hero__aside">
          <div className="home-hero__support">
            <span className="home-hero__support-label">Рабочий контур</span>
            <strong>Единая панель управления группами</strong>
            <p>React-панель использует те же серверные маршруты, но собирает навигацию, статистику и настройки в одно плотное рабочее пространство.</p>
          </div>

          <div className="home-hero__spotlight">
            {spotlightMetrics.map((metric) => (
              <article key={`spotlight-${metric.label}`} className="home-hero__spotlight-card">
                <span>{metric.label}</span>
                <strong>{metric.value}</strong>
                <p>{metric.note}</p>
              </article>
            ))}
          </div>
        </div>
      </section>

      <section className="home-action-grid" aria-label="Быстрые действия">
        {quickActions.map((card) => (
          <Link key={card.title} className="home-action-card" to={card.href}>
            <span className="home-action-card__icon">
              <PanelGlyph kind={card.icon} />
            </span>
            <div className="home-action-card__copy">
              <span className="home-action-card__label">{card.label}</span>
              <strong>{card.title}</strong>
              <p>{card.text}</p>
            </div>
          </Link>
        ))}
      </section>

      <section className="home-metrics">
        {data.metrics.map((metric) => (
          <article key={metric.label} className={`home-metric home-metric--${metric.tone}`}>
            <span className="home-metric__label">{metric.label}</span>
            <strong className="home-metric__value">{metric.value}</strong>
            <span className="home-metric__note">{metric.note}</span>
          </article>
        ))}
      </section>

      <section className="home-grid home-grid--two-col">
        <GroupSection title="Админ-группы" groups={data.admin_groups} />
        <GroupSection title="Недавние группы" groups={data.activity_groups} />
      </section>

      <section className="home-grid home-grid--two-col">
        <article className="home-panel">
          <div className="home-panel__head">
            <h2>{data.global_dashboard.title}</h2>
          </div>

          {data.global_dashboard.empty_text ? (
            <p className="home-empty">{data.global_dashboard.empty_text}</p>
          ) : (
            <div className="home-stat-list">
              {data.global_dashboard.rows.map((row) => (
                <div key={row.title} className="home-stat-row">
                  <div>
                    <strong>{row.title}</strong>
                    <p>{row.meta}</p>
                  </div>
                  <span>{row.value}</span>
                </div>
              ))}
            </div>
          )}
        </article>

        <article className="home-panel">
          <div className="home-panel__head">
            <h2>Безопасность</h2>
          </div>

          <div className="home-security-list">
            {data.security_items.map((item) => (
              <div key={item.title} className="home-security-card">
                <strong>{item.title}</strong>
                <p>{item.text}</p>
              </div>
            ))}
          </div>
        </article>
      </section>
    </div>
  )
}
