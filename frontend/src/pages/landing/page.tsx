import { useQuery } from '@tanstack/react-query'

import { getLandingContext } from '@/pages/landing/api/get-landing-context'
import { usePageTitle } from '@/shared/lib/use-page-title'
import { resolveAppPath } from '@/shared/config/app-base-path'

function isExternal(href: string) {
  return href.startsWith('http://') || href.startsWith('https://')
}

export function LandingPage() {
  const landingQuery = useQuery({
    queryKey: ['landing-context'],
    queryFn: getLandingContext,
  })

  usePageTitle('Лендинг')

  if (landingQuery.isLoading) {
    return (
      <section className="public-page">
        <div className="page-status">
          <span className="page-card__eyebrow">Selara</span>
          <strong>Загружаю лендинг…</strong>
        </div>
      </section>
    )
  }

  if (landingQuery.isError) {
    return (
      <section className="public-page">
        <div className="page-status page-status--error">
          <span className="page-card__eyebrow">Selara</span>
          <strong>Не удалось загрузить лендинг</strong>
          <p>{landingQuery.error.message}</p>
        </div>
      </section>
    )
  }

  const data = landingQuery.data

  if (!data) {
    return (
      <section className="public-page">
        <div className="page-status">
          <strong>Данные лендинга пока недоступны.</strong>
        </div>
      </section>
    )
  }

  return (
    <section className="public-page">
      <div className="public-login">
        <section className="public-card public-card--hero">
          <span className="page-card__eyebrow">{data.hero_eyebrow}</span>
          {data.session_note ? <div className="public-route-note">{data.session_note}</div> : null}
          <div>
            <h1>
              <span>{data.hero_title_primary}</span>
              <br />
              <strong>{data.hero_title_secondary}</strong>
            </h1>
            <p className="public-copy">{data.hero_subtitle}</p>
          </div>
          <div className="public-actions">
            {data.hero_ctas.map((cta, index) => (
              <a
                key={`${cta.href}-${cta.label}`}
                className={index === 0 ? 'button button--primary' : 'button button--secondary'}
                href={resolveAppPath(cta.href)}
                target={isExternal(cta.href) ? '_blank' : undefined}
                rel={isExternal(cta.href) ? 'noreferrer' : undefined}
              >
                {cta.label}
              </a>
            ))}
          </div>
          <div className="public-stat-grid">
            {data.signal_cards.map((card) => (
              <article key={card.label} className="public-metric">
                <span className="page-card__eyebrow">{card.label}</span>
                <strong>{card.value}</strong>
                <p>{card.note}</p>
              </article>
            ))}
          </div>
          <span className="public-copy">{data.developer_credit}</span>
        </section>

        <section className="public-metrics">
          {data.metrics.map((metric) => (
            <article key={metric.label} className="public-metric">
              <span className="page-card__eyebrow">{metric.label}</span>
              <strong>{metric.value}</strong>
              <span className="public-metric__note">{metric.note}</span>
            </article>
          ))}
        </section>

        <div className="public-login__grid">
          <article className="public-card">
            <span className="page-card__eyebrow">Что внутри Selara</span>
            <h2>Один бот для жизни группы</h2>
            <p className="public-copy">{data.overview_text}</p>
            <div className="public-actions">
              {data.overview_pills.map((pill) => (
                <span key={pill} className="public-hero-chip">
                  {pill}
                </span>
              ))}
            </div>
          </article>

          <article className="public-card">
            <span className="page-card__eyebrow">Как начать</span>
            <h2>Путь от Telegram до кабинета</h2>
            <div className="public-step-grid">
              {data.step_cards.map((step) => (
                <article key={step.step} className="public-step-card">
                  <strong>{step.step}</strong>
                  <div>
                    <strong>{step.title}</strong>
                    <p>{step.text}</p>
                  </div>
                </article>
              ))}
            </div>
          </article>
        </div>

        <section className="public-feature-grid">
          {data.feature_cards.map((feature) => (
            <article key={feature.title} className="public-feature-card">
              <span className="page-card__eyebrow">{feature.kicker}</span>
              <h2>{feature.title}</h2>
              <p>{feature.text}</p>
              <div className="public-feature-list">
                {feature.items.map((item) => (
                  <span key={`${feature.title}-${item}`} className="public-hero-chip">
                    {item}
                  </span>
                ))}
              </div>
              {feature.href ? (
                <a
                  className="button button--secondary"
                  href={resolveAppPath(feature.href)}
                  target={isExternal(feature.href) ? '_blank' : undefined}
                  rel={isExternal(feature.href) ? 'noreferrer' : undefined}
                >
                  {feature.link_label}
                </a>
              ) : null}
            </article>
          ))}
        </section>

        <section className="public-card">
          <span className="page-card__eyebrow">Нужные ссылки</span>
          <h2>Все основные маршруты в одном месте</h2>
          <div className="public-route-grid">
            {data.route_cards.map((route) => (
              <a
                key={`${route.title}-${route.href}`}
                className="public-route-card"
                href={resolveAppPath(route.href)}
                target={isExternal(route.href) ? '_blank' : undefined}
                rel={isExternal(route.href) ? 'noreferrer' : undefined}
              >
                <span className="public-route-note">{route.note}</span>
                <strong>{route.title}</strong>
                <code>{route.display_href}</code>
                <p>{route.description}</p>
              </a>
            ))}
          </div>
        </section>
      </div>
    </section>
  )
}
