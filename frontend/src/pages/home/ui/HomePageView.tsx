import { Link } from 'react-router-dom'

import type { HomeGroupLink, HomePageData } from '@/pages/home/model/types'

import './home-page.css'

type HomePageViewProps = {
  data: HomePageData
}

function badgeLabel(value: string) {
  if (value === 'admin') {
    return 'админ'
  }

  return 'участник'
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
          {groups.map((group) => (
            <Link key={group.href} className="home-list-card" to={group.href}>
              <div>
                <strong>{group.title}</strong>
                <p>{group.meta}</p>
              </div>
              <span className={`home-pill home-pill--${group.badge}`}>{badgeLabel(group.badge)}</span>
            </Link>
          ))}
        </div>
      ) : (
        <p className="home-empty">Пока нет данных для этого блока.</p>
      )}
    </article>
  )
}

export function HomePageView({ data }: HomePageViewProps) {
  return (
    <div className="home-page">
      <section className="home-hero">
        <div>
          <span className="page-card__eyebrow">Личный кабинет</span>
          <h1>{data.hero_title}</h1>
          <p>{data.hero_subtitle}</p>
        </div>
        <div className="home-hero__chips">
          <span className="home-chip">Web и Telegram</span>
          <span className="home-chip">Вход по одноразовому коду</span>
          <span className="home-chip">Статистика по группам</span>
        </div>
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
