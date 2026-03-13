import { Link } from 'react-router-dom'

import type { AuditPageData } from '@/pages/audit/model/types'
import { routes } from '@/shared/config/routes'
import { ChatSectionNav } from '@/shared/ui/chat-section-nav/ChatSectionNav'

import './audit-page.css'

type AuditPageViewProps = {
  chatId: string
  data: AuditPageData
}

export function AuditPageView({ chatId, data }: AuditPageViewProps) {
  return (
    <div className="audit-page">
      <section className="audit-hero">
        <div>
          <span className="page-card__eyebrow">Журнал событий</span>
          <h1>{data.chat_title}</h1>
          <p>Хронология админских действий и событий бота в группе.</p>
        </div>
        <div className="audit-hero__chips">
          <span className="audit-chip">ID чата {chatId}</span>
          <span className="audit-chip">Последние 200 записей</span>
          <Link className="button" to={routes.chat(chatId)}>
            К группе
          </Link>
        </div>
      </section>

      <ChatSectionNav links={data.chat_section_links} />

      <section className="audit-panel">
        <div className="audit-panel__head">
          <div>
            <h2>Лента событий</h2>
            <p>Свежие изменения настроек, триггеров, алиасов и действий бота в чате.</p>
          </div>
        </div>

        {data.audit_rows.length > 0 ? (
          <div className="audit-list">
            {data.audit_rows.map((row, index) => (
              <div key={`${row.when}-${row.action}-${index}`} className="audit-row">
                <div>
                  <strong>{row.action}</strong>
                  <p>{row.description}</p>
                  <p>
                    Инициатор: {row.actor} • Цель: {row.target}
                  </p>
                </div>
                <span>{row.when}</span>
              </div>
            ))}
          </div>
        ) : (
          <p className="audit-empty">Логи пока пусты.</p>
        )}
      </section>
    </div>
  )
}
