import type { ChatAchievementsData } from '@/pages/chat/model/types'
import { ChatSectionNav } from '@/shared/ui/chat-section-nav/ChatSectionNav'
import { AchievementCard } from '@/shared/ui/achievement-card/AchievementCard'

import './chat-page.css'

type ChatAchievementsViewProps = {
  chatId: string
  data: ChatAchievementsData
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

      <ChatSectionNav chatId={chatId} active="achievements" canManageSettings={data.can_manage_settings} />

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
                  <AchievementCard key={`${section.title}-${row.title}`} row={row} />
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
