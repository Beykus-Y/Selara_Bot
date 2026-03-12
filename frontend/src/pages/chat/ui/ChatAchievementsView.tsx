import { Link } from 'react-router-dom'

import type { ChatAchievementsData } from '@/pages/chat/model/types'
import { routes } from '@/shared/config/routes'

import './chat-page.css'

type ChatAchievementsViewProps = {
  chatId: string
  data: ChatAchievementsData
}

function tabHref(chatId: string, tab: 'overview' | 'achievements' | 'settings') {
  return `/app/chat/${chatId}?tab=${tab}`
}

export function ChatAchievementsView({ chatId, data }: ChatAchievementsViewProps) {
  return (
    <div className="chat-page">
      <section className="chat-hero">
        <div>
          <span className="page-card__eyebrow">Группа</span>
          <h1>{data.chat_title}</h1>
          <p>Локальные достижения внутри этой группы доступны прямо в браузере.</p>
        </div>
        <div className="chat-hero__chips">
          <span className="chat-chip">ID чата {chatId}</span>
          <span className="chat-chip">Достижения группы</span>
          <span className="chat-chip">Локальные награды</span>
        </div>
      </section>

      <section className="chat-tabs">
        <Link className="button" to={tabHref(chatId, 'overview')}>
          Обзор
        </Link>
        <Link className="button button--primary" to={tabHref(chatId, 'achievements')}>
          Достижения
        </Link>
        {data.can_manage_settings ? (
          <Link className="button" to={tabHref(chatId, 'settings')}>
            Настройки
          </Link>
        ) : null}
        <Link className="button" to={routes.economy(chatId)}>
          Экономика
        </Link>
        <Link className="button" to={routes.family(chatId)}>
          Моя семья
        </Link>
      </section>

      <section className="chat-achievements-grid">
        {data.achievement_sections.map((section) => (
          <article key={section.title} className="chat-panel">
            <div className="chat-panel__head">
              <div>
                <h2>{section.title}</h2>
                <p>Локальные достижения внутри этой группы.</p>
              </div>
              <span className="chat-panel__tag">{section.rows.length}</span>
            </div>

            {section.rows.length > 0 ? (
              <div className="chat-achievements-list">
                {section.rows.map((row) => (
                  <div key={`${section.title}-${row.title}`} className="chat-achievement-row">
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
              <p className="chat-status">Пока нет локальных достижений для этой группы.</p>
            )}
          </article>
        ))}
      </section>
    </div>
  )
}
