import type { AchievementsPageData } from '@/pages/achievements/model/types'

import './achievements-page.css'

type AchievementsPageViewProps = {
  data: AchievementsPageData
}

const helperCards = [
  {
    title: 'Сообщения и стрики',
    text: 'Чатовые достижения считаются внутри конкретной группы и доступны прямо на её странице.',
  },
  {
    title: 'Социальные статусы',
    text: 'Пара, брак, питомцы и коллекции достижений обновляются через события и ручное обновление профиля.',
  },
  {
    title: 'Telegram-команды',
    text: 'Используй /achievements, достижения или мои ачивки внутри группы.',
  },
]

export function AchievementsPageView({ data }: AchievementsPageViewProps) {
  return (
    <div className="achievements-page">
      <section className="achievements-hero">
        <div>
          <span className="page-card__eyebrow">Аккаунт</span>
          <h1>{data.hero_title}</h1>
          <p>{data.hero_subtitle}</p>
        </div>
        <div className="achievements-hero__chips">
          <span className="achievements-chip">Глобальные достижения</span>
          <span className="achievements-chip">Telegram и браузер</span>
          <span className="achievements-chip">Единый каталог</span>
        </div>
      </section>

      <section className="achievements-metrics">
        {data.achievement_metrics.map((metric) => (
          <article key={metric.label} className={`achievements-metric achievements-metric--${metric.tone}`}>
            <span className="achievements-metric__label">{metric.label}</span>
            <strong className="achievements-metric__value">{metric.value}</strong>
            <span className="achievements-metric__note">{metric.note}</span>
          </article>
        ))}
      </section>

      <section className="achievements-grid">
        {data.achievement_sections.map((section) => (
          <article key={section.title} className="achievements-panel">
            <div className="achievements-panel__head">
              <div>
                <h2>{section.title}</h2>
                <p>Глобальные достижения аккаунта, не привязанные к одной группе.</p>
              </div>
              <span className="achievements-panel__tag">{section.rows.length}</span>
            </div>

            {section.rows.length > 0 ? (
              <div className="achievements-list">
                {section.rows.map((row) => (
                  <div key={`${section.title}-${row.title}`} className="achievements-row">
                    <div>
                      <strong>{row.title}</strong>
                      <p>{row.meta}</p>
                      <p>{row.description}</p>
                    </div>
                    <span>{row.value}</span>
                  </div>
                ))}
              </div>
            ) : (
              <p className="achievements-empty">Глобальные достижения пока не появились.</p>
            )}
          </article>
        ))}

        <article className="achievements-panel">
          <div className="achievements-panel__head">
            <div>
              <h2>Как открываются</h2>
              <p>Часть достижений приходит из активности, часть из социальных и семейных механик.</p>
            </div>
          </div>

          <div className="achievements-help-list">
            {helperCards.map((card) => (
              <div key={card.title} className="achievements-help-card">
                <strong>{card.title}</strong>
                <p>{card.text}</p>
              </div>
            ))}
          </div>
        </article>
      </section>
    </div>
  )
}
