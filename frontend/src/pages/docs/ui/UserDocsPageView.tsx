import type { UserDocsPageData, UserDocsItem } from '@/pages/docs/model/types'

import './docs-page.css'

type UserDocsPageViewProps = {
  data: UserDocsPageData
}

function DocsList({ title, items, code = false }: { title: string; items: string[]; code?: boolean }) {
  return (
    <div className="docs-card-block">
      <span className="docs-card-label">{title}</span>
      <ul className={code ? 'docs-bullet-list docs-code-list' : 'docs-bullet-list'}>
        {items.map((item) => (
          <li key={`${title}-${item}`}>{code ? <code>{item}</code> : item}</li>
        ))}
      </ul>
    </div>
  )
}

function DocsCard({ item }: { item: UserDocsItem }) {
  return (
    <article className="docs-card">
      <div className="docs-card-head">
        <div className="docs-card-title-stack">
          <strong>{item.title}</strong>
          {item.badges && item.badges.length > 0 ? (
            <div className="docs-chip-row">
              {item.badges.map((badge) => (
                <span key={`${item.title}-${badge}`} className="docs-chip">
                  {badge}
                </span>
              ))}
            </div>
          ) : null}
        </div>
      </div>
      <p className="docs-copy">{item.text}</p>

      {item.commands && item.commands.length > 0 ? <DocsList title="Команды" items={item.commands} code /> : null}
      {item.triggers && item.triggers.length > 0 ? <DocsList title="Триггеры" items={item.triggers} code /> : null}
      {item.examples && item.examples.length > 0 ? <DocsList title="Примеры" items={item.examples} code /> : null}
      {item.steps && item.steps.length > 0 ? <DocsList title="Как использовать" items={item.steps} /> : null}
      {item.notes && item.notes.length > 0 ? <DocsList title="Важно" items={item.notes} /> : null}
    </article>
  )
}

export function UserDocsPageView({ data }: UserDocsPageViewProps) {
  return (
    <div className="docs-page">
      <section className="docs-hero">
        <div>
          <span className="page-card__eyebrow">Документация</span>
          <h1>{data.hero_title}</h1>
          <p>{data.hero_subtitle}</p>
          {data.origin_chat ? (
            <div className="docs-hero__actions">
              <a className="button button--primary" href={data.origin_chat.href}>
                Вернуться в {data.origin_chat.label}
              </a>
            </div>
          ) : null}
        </div>
        <div className="docs-hero__chips">
          {data.hero_chips.map((chip) => (
            <span key={chip} className="docs-chip">
              {chip}
            </span>
          ))}
        </div>
      </section>

      <section className="docs-layout">
        <aside className="docs-nav">
          <div className="docs-panel-head">
            <h2>Навигация</h2>
          </div>
          <div className="docs-nav-stack">
            <div className="docs-nav-group">
              <strong>Разделы справки</strong>
              {data.docs_sections.map((section) => (
                <a key={section.anchor} href={`#${section.anchor}`}>
                  {section.title}
                </a>
              ))}
            </div>
          </div>
        </aside>

        <div className="docs-content">
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
                  <DocsCard key={`${section.anchor}-${item.title}`} item={item} />
                ))}
              </div>
            </section>
          ))}
        </div>
      </section>
    </div>
  )
}
