import { Link } from 'react-router-dom'

import type { BroadcastPageData } from '@/pages/admin-broadcast/model/types'
import { routes } from '@/shared/config/routes'

import './broadcast-page.css'

type BroadcastPageViewProps = {
  data: BroadcastPageData
}

export function BroadcastPageView({ data }: BroadcastPageViewProps) {
  const { broadcast, deliveries, replies } = data

  return (
    <div className="broadcast-detail-page">
      <article className="broadcast-detail-panel">
        <div className="broadcast-detail-panel__head">
          <div>
            <h2>Рассылка #{broadcast.id}</h2>
            <p className="broadcast-detail-panel__subtitle">
              История доставки и ответы пользователей реплаем на системное сообщение.
            </p>
          </div>
          <Link className="button ghost" to={routes.admin}>Назад в админку</Link>
        </div>

        <div className="broadcast-detail-summary">
          <article className="broadcast-stat-card">
            <span className="broadcast-stat-label">Создано</span>
            <strong>{broadcast.created_at}</strong>
            <small>активные чаты за {broadcast.active_since_days} дня</small>
          </article>
          <article className="broadcast-stat-card">
            <span className="broadcast-stat-label">Доставлено</span>
            <strong>{broadcast.sent_count} / {broadcast.target_count}</strong>
            <small>ошибок: {broadcast.failed_count}</small>
          </article>
          <article className="broadcast-stat-card">
            <span className="broadcast-stat-label">Ответов реплаем</span>
            <strong>{broadcast.reply_count}</strong>
            <small>собраны в базе и доступны ниже</small>
          </article>
        </div>

        {/* Message preview */}
        <div className="broadcast-section">
          <h3>Как выглядело сообщение</h3>
          <p className="broadcast-section-subtitle">Отправлялось в HTML-формате от имени бота.</p>
          <div
            className="broadcast-preview-box"
            dangerouslySetInnerHTML={{ __html: broadcast.message_preview_html }}
          />
        </div>

        {/* Deliveries */}
        <div className="broadcast-section">
          <h3>Доставки</h3>
          <p className="broadcast-section-subtitle">Каждый чат, который попал в рассылку.</p>
          {deliveries.length > 0 ? (
            <div className="broadcast-delivery-list">
              {deliveries.map((item, idx) => (
                <article
                  key={`${item.chat_id}-${idx}`}
                  className={`broadcast-delivery-card broadcast-delivery-card--${item.status_code}`}
                >
                  <div className="broadcast-delivery-head">
                    <div>
                      <strong>{item.chat_title}</strong>
                      <p className="broadcast-delivery-meta-text">
                        chat_id={item.chat_id} · активность: {item.last_activity_at}
                      </p>
                    </div>
                    <span className={`broadcast-delivery-status broadcast-delivery-status--${item.status_tone}`}>
                      {item.status_label}
                    </span>
                  </div>
                  <div className="broadcast-delivery-info">
                    <span>message_id: <code>{item.telegram_message_id ?? '—'}</code></span>
                    <span>ответов: <code>{item.reply_count}</code></span>
                    <span>дата: {item.sent_at}</span>
                  </div>
                  {item.error_text && (
                    <p className="broadcast-error-text">{item.error_text}</p>
                  )}
                </article>
              ))}
            </div>
          ) : (
            <p className="broadcast-empty">Записей о доставке пока нет.</p>
          )}
        </div>

        {/* Replies */}
        <div className="broadcast-section">
          <h3>Ответы реплаем</h3>
          <p className="broadcast-section-subtitle">Сохраняются только прямые ответы на сообщение рассылки.</p>
          {replies.length > 0 ? (
            <div className="broadcast-reply-list">
              {replies.map((item, idx) => (
                <article key={idx} className="broadcast-reply-card">
                  <div className="broadcast-reply-head">
                    <div>
                      <strong>{item.user_label}</strong>
                      <p className="broadcast-reply-meta">{item.chat_title} · {item.sent_at}</p>
                    </div>
                    <span className="broadcast-type-badge">{item.message_type}</span>
                  </div>
                  <p className="broadcast-reply-preview">{item.preview}</p>
                </article>
              ))}
            </div>
          ) : (
            <p className="broadcast-empty">Пока никто не ответил реплаем на эту рассылку.</p>
          )}
        </div>
      </article>
    </div>
  )
}
