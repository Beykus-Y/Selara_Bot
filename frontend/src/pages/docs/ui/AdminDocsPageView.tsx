import type { AdminDocsFeatureItem, AdminDocsPageData } from '@/pages/docs/model/types'
import { resolveAppPath } from '@/shared/config/app-base-path'

import './docs-page.css'

type AdminDocsPageViewProps = {
  data: AdminDocsPageData
}

function AdminFeatureCard({ item }: { item: AdminDocsFeatureItem }) {
  return (
    <article className="docs-card">
      <div className="docs-card-head">
        <strong>{item.title}</strong>
      </div>
      <p className="docs-copy">{item.text}</p>
    </article>
  )
}

export function AdminDocsPageView({ data }: AdminDocsPageViewProps) {
  return (
    <div className="docs-page">
      <section className="docs-hero">
        <div>
          <span className="page-card__eyebrow">Документация</span>
          <h1>{data.hero_title}</h1>
          <p>{data.hero_subtitle}</p>
          {data.origin_chat ? (
            <div className="docs-hero__actions">
              <a className="button button--primary" href={resolveAppPath(data.origin_chat.href)}>
                Вернуться в {data.origin_chat.label}
              </a>
            </div>
          ) : null}
        </div>
        <div className="docs-hero__chips">
          <span className="docs-chip">руководство администратора</span>
          <span className="docs-chip">настройки и доступы</span>
          <span className="docs-chip">сценарии панели</span>
        </div>
      </section>

      <section className="docs-layout">
        <aside className="docs-nav">
          <div className="docs-panel-head">
            <h2>Навигация</h2>
          </div>
          <div className="docs-nav-stack">
            <div className="docs-nav-group">
              <strong>Разделы панели</strong>
              <a href="#docs-trigger-types">Типы совпадения триггеров</a>
              <a href="#docs-trigger-variables">Переменные шаблонов</a>
              {data.docs_sections.map((section) => (
                <a key={section.anchor} href={`#${section.anchor}`}>
                  {section.title}
                </a>
              ))}
            </div>
            <div className="docs-nav-group">
              <strong>Группы настроек</strong>
              {data.settings_docs_sections.map((section) => (
                <a key={section.anchor} href={`#${section.anchor}`}>
                  {section.title}
                </a>
              ))}
            </div>
          </div>
        </aside>

        <div className="docs-content">
          <section className="docs-section" id="docs-trigger-types">
            <div className="docs-panel-head">
              <div>
                <h2>Типы совпадения смарт-триггеров</h2>
                <p>Те же значения используются в формах панели, но показываются на русском.</p>
              </div>
            </div>
            <div className="docs-card-grid">
              {data.trigger_match_types.map((item) => (
                <article key={item.code} className="docs-card">
                  <div className="docs-card-head">
                    <strong>{item.label}</strong>
                    <code>{item.code}</code>
                  </div>
                  <p className="docs-copy">{item.description}</p>
                </article>
              ))}
            </div>
          </section>

          <section className="docs-section" id="docs-trigger-variables">
            <div className="docs-panel-head">
              <div>
                <h2>Переменные шаблонов</h2>
                <p>Список общий для смарт-триггеров и кастомных RP-действий.</p>
              </div>
            </div>
            {data.trigger_template_variable_groups.map((group) => (
              <div key={group.title} className="docs-section-stack">
                <div className="docs-panel-head">
                  <div>
                    <h3>{group.title}</h3>
                  </div>
                </div>
                <div className="docs-card-grid">
                  {group.items.map((item) => (
                    <article key={item.token} className="docs-card">
                      <div className="docs-card-head">
                        <strong>{item.label}</strong>
                        <code>{item.token}</code>
                      </div>
                      <p className="docs-copy">{item.description}</p>
                      <p className="docs-copy">Доступность: {item.availability}</p>
                      <p className="docs-copy">Алиасы: {item.aliases}</p>
                    </article>
                  ))}
                </div>
              </div>
            ))}
          </section>

          {data.docs_sections.map((section) => (
            <section key={section.anchor} className="docs-section" id={section.anchor}>
              <div className="docs-panel-head">
                <div>
                  <h2>{section.title}</h2>
                  <p>{section.summary}</p>
                </div>
              </div>
              <div className="docs-card-grid">
                {section.items.map((item) => (
                  <AdminFeatureCard key={`${section.anchor}-${item.title}`} item={item} />
                ))}
              </div>
            </section>
          ))}

          {data.settings_docs_sections.map((section) => (
            <section key={section.anchor} className="docs-section" id={section.anchor}>
              <div className="docs-panel-head">
                <div>
                  <h2>{section.title}</h2>
                  <p>Подробности по каждому ключу этой группы с быстрыми якорями и форматом значения.</p>
                </div>
              </div>
              <div className="docs-settings-index-grid">
                {section.items.map((item, index) => (
                  <a key={item.anchor} className="docs-index-card" href={`#${item.anchor}`}>
                    <span className="docs-index-no">{index + 1}</span>
                    <span className="docs-index-copy">
                      <strong>{item.title}</strong>
                      <small>{item.key}</small>
                    </span>
                  </a>
                ))}
              </div>
              <div className="docs-settings-grid">
                {section.items.map((item, index) => (
                  <article key={item.anchor} className="docs-setting-card" id={item.anchor}>
                    <div className="docs-setting-kicker-row">
                      <span className="docs-setting-kicker">Настройка</span>
                      <span className="docs-setting-no">{index + 1}</span>
                    </div>
                    <div className="docs-card-head docs-setting-head">
                      <strong>{item.title}</strong>
                      <code>{item.key}</code>
                    </div>
                    <p className="docs-copy">{item.description}</p>
                    <div className="docs-setting-meta">
                      <div className="docs-meta-box">
                        <span>Формат</span>
                        <strong>{item.value_hint}</strong>
                      </div>
                      <div className="docs-meta-box">
                        <span>Раздел панели</span>
                        <strong>Настройки чата -&gt; {section.title}</strong>
                      </div>
                    </div>
                  </article>
                ))}
              </div>
            </section>
          ))}
        </div>
      </section>
    </div>
  )
}
