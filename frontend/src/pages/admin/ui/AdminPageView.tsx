import { useEffect, useMemo, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useMutation, useQueryClient } from '@tanstack/react-query'

import {
  adminLogout,
  adminRequestBackup,
  adminSendBroadcast,
  adminUpdateFeedbackStatus,
} from '@/pages/admin/api/admin-actions'
import type { AdminPageData } from '@/pages/admin/model/types'
import { routes } from '@/shared/config/routes'

import './admin-page.css'

type AdminPageViewProps = {
  data: AdminPageData
}

export function AdminPageView({ data }: AdminPageViewProps) {
  const navigate = useNavigate()
  const queryClient = useQueryClient()

  const [broadcastBody, setBroadcastBody] = useState('')
  const [mediaMode, setMediaMode] = useState<'text' | 'photo'>('text')
  const [photo, setPhoto] = useState<File | null>(null)
  const [reactionsEnabled, setReactionsEnabled] = useState(false)
  const [reactionRows, setReactionRows] = useState([
    { emoji: '👍', label: 'Всё понятно' },
    { emoji: '👎', label: 'Не согласен' },
  ])
  const [selectedChatIds, setSelectedChatIds] = useState<Set<number>>(
    new Set(data.recent_active_chats.filter((c) => c.checked).map((c) => c.chat_id)),
  )
  const [flash, setFlash] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const logoutMutation = useMutation({
    mutationFn: adminLogout,
    onSuccess: () => void navigate(routes.adminLogin),
    onError: (err: Error) => setError(err.message),
  })

  const backupMutation = useMutation({
    mutationFn: adminRequestBackup,
    onSuccess: (msg) => { setFlash(msg); setError(null) },
    onError: (err: Error) => setError(err.message),
  })

  const photoPreview = useMemo(() => photo ? URL.createObjectURL(photo) : null, [photo])
  useEffect(() => () => {
    if (photoPreview) URL.revokeObjectURL(photoPreview)
  }, [photoPreview])

  const compiledBroadcastBody = useMemo(() => {
    if (!reactionsEnabled) return broadcastBody.trim()
    const rows = reactionRows.map((row) => `${row.emoji.trim()} = ${row.label.trim()}`).join('\n')
    return `${broadcastBody.trim()}\n\n[reactions]\n${rows}\n[/reactions]`
  }, [broadcastBody, reactionRows, reactionsEnabled])

  const broadcastMutation = useMutation({
    mutationFn: () => {
      if (selectedChatIds.size === 0) throw new Error('Выберите хотя бы один чат.')
      if (mediaMode === 'photo' && !photo) throw new Error('Выберите фотографию.')
      if (reactionsEnabled && reactionRows.some((row) => !row.emoji.trim() || !row.label.trim())) {
        throw new Error('Заполните emoji и описание каждой реакции.')
      }
      return adminSendBroadcast(compiledBroadcastBody, Array.from(selectedChatIds), mediaMode, photo)
    },
    onSuccess: (result) => {
      setFlash(result.message)
      setError(null)
      setBroadcastBody('')
      setPhoto(null)
      void navigate(routes.adminBroadcast(result.broadcast_id))
    },
    onError: (err: Error) => setError(err.message),
  })

  const feedbackMutation = useMutation({
    mutationFn: ({ id, status }: { id: number; status: 'open' | 'done' }) =>
      adminUpdateFeedbackStatus(id, status),
    onSuccess: (msg) => {
      setFlash(msg)
      setError(null)
      void queryClient.invalidateQueries({ queryKey: ['admin-page'] })
    },
    onError: (err: Error) => setError(err.message),
  })

  function toggleChat(chatId: number) {
    setSelectedChatIds((prev) => {
      const next = new Set(prev)
      if (next.has(chatId)) next.delete(chatId)
      else next.add(chatId)
      return next
    })
  }

  function selectAll() {
    setSelectedChatIds(new Set(data.recent_active_chats.map((c) => c.chat_id)))
  }

  function selectNone() {
    setSelectedChatIds(new Set())
  }

  return (
    <div className="admin-page">
      {flash && <div className="admin-flash">{flash}</div>}
      {error && <div className="admin-error">{error}</div>}

      <article className="admin-panel">
        <div className="admin-panel__head">
          <div>
            <h2>Админ-панель</h2>
            <p className="admin-panel__subtitle">Управление базой данных и просмотр состояния бота.</p>
          </div>
        </div>

        <div className="admin-user-info">
          <span className="badge badge-ok">Админ</span>
          <span>User ID: <code>{data.admin_user_id}</code></span>
          <span>Открытых заявок: <code>{data.open_feedback_count}</code></span>
        </div>

        {/* Broadcast section */}
        <div className="admin-section">
          <div className="admin-section-head">
            <h3>Системная рассылка</h3>
            <p className="admin-panel__subtitle">
              Сообщение уйдёт только в активные чаты за последние {data.broadcast_active_days} дня.
            </p>
          </div>

          <div className="broadcast-grid">
            <form
              className="broadcast-composer"
              onSubmit={(e) => {
                e.preventDefault()
                broadcastMutation.mutate()
              }}
            >
              <div>
                <label className="broadcast-label" htmlFor="broadcast-body">Текст сообщения</label>
                <textarea
                  id="broadcast-body"
                  className="broadcast-textarea"
                  maxLength={3200}
                  placeholder="Спасибо за использование Selara. Мы продолжаем развивать бота и будем рады вашей обратной связи."
                  required
                  value={broadcastBody}
                  onChange={(e) => setBroadcastBody(e.target.value)}
                />
                <small className="broadcast-counter">
                  Исходник: {compiledBroadcastBody.length} · лимит результата: {mediaMode === 'photo' ? 1024 : 3200}
                </small>
              </div>
              <details className="broadcast-format-help">
                <summary>Всё форматирование этой формы (Telegram HTML)</summary>
                <div className="broadcast-format-help__content">
                  <p><strong>Жирный:</strong> <code>&lt;b&gt;текст&lt;/b&gt;</code> или <code>&lt;strong&gt;</code>.</p>
                  <p><strong>Курсив:</strong> <code>&lt;i&gt;текст&lt;/i&gt;</code> или <code>&lt;em&gt;</code>.</p>
                  <p><strong>Подчёркивание:</strong> <code>&lt;u&gt;текст&lt;/u&gt;</code> или <code>&lt;ins&gt;</code>.</p>
                  <p><strong>Зачёркивание:</strong> <code>&lt;s&gt;</code>, <code>&lt;strike&gt;</code> или <code>&lt;del&gt;</code>.</p>
                  <p>
                    <strong>Спойлер:</strong> <code>&lt;tg-spoiler&gt;текст&lt;/tg-spoiler&gt;</code> или{' '}
                    <code>&lt;span class=&quot;tg-spoiler&quot;&gt;текст&lt;/span&gt;</code>.
                  </p>
                  <p>
                    <strong>Ссылка/упоминание:</strong>{' '}
                    <code>&lt;a href=&quot;https://example.com&quot;&gt;ссылка&lt;/a&gt;</code> или{' '}
                    <code>&lt;a href=&quot;tg://user?id=123&quot;&gt;пользователь&lt;/a&gt;</code>. Также разрешены{' '}
                    <code>http:</code>, <code>mailto:</code> и <code>tel:</code>.
                  </p>
                  <p>
                    <strong>Моноширинный текст:</strong> <code>&lt;code&gt;код&lt;/code&gt;</code>; блок —{' '}
                    <code>&lt;pre&gt;код&lt;/pre&gt;</code>; язык —{' '}
                    <code>&lt;pre&gt;&lt;code class=&quot;language-python&quot;&gt;...&lt;/code&gt;&lt;/pre&gt;</code>.
                  </p>
                  <p>
                    <strong>Цитата:</strong> <code>&lt;blockquote&gt;текст&lt;/blockquote&gt;</code>; сворачиваемая —{' '}
                    <code>&lt;blockquote expandable&gt;текст&lt;/blockquote&gt;</code>.
                  </p>
                  <p>
                    <strong>Кастомный emoji:</strong>{' '}
                    <code>&lt;tg-emoji emoji-id=&quot;5368324170671202286&quot;&gt;👍&lt;/tg-emoji&gt;</code>. Доступность зависит от
                    Premium владельца бота или дополнительных username.
                  </p>
                  <p>
                    <strong>Дата/время:</strong>{' '}
                    <code>&lt;tg-time unix=&quot;1647531900&quot; format=&quot;wDT&quot;&gt;время&lt;/tg-time&gt;</code>.
                  </p>
                  <p>
                    Обычные символы <code>&lt;</code>, <code>&gt;</code>, <code>&amp;</code> и <code>&quot;</code> пишутся как{' '}
                    <code>&amp;lt;</code>, <code>&amp;gt;</code>, <code>&amp;amp;</code> и <code>&amp;quot;</code>. Markdown вроде{' '}
                    <code>**жирный**</code> здесь не обрабатывается.
                  </p>
                  <p>
                    Реакции вручную: <code>[reactions]</code>, затем 2–6 строк вида <code>👍 = Всё понятно</code>,
                    затем <code>[/reactions]</code>. Проще включить конструктор ниже.
                  </p>
                  <p>
                    Лимиты формы: текст — 3200 символов, подпись к фото — 1024 после добавления расшифровки реакций;
                    JPEG/PNG — до 10 МБ.
                  </p>
                </div>
              </details>

              <fieldset className="broadcast-options">
                <legend className="broadcast-label">Формат публикации</legend>
                <label><input type="radio" checked={mediaMode === 'text'} onChange={() => setMediaMode('text')} /> Текст</label>
                <label><input type="radio" checked={mediaMode === 'photo'} onChange={() => setMediaMode('photo')} /> Фото с подписью</label>
              </fieldset>
              {mediaMode === 'photo' && (
                <div className="broadcast-photo-field">
                  <label className="broadcast-label" htmlFor="broadcast-photo">JPEG или PNG, до 10 МБ</label>
                  <input
                    id="broadcast-photo"
                    type="file"
                    accept="image/jpeg,image/png"
                    onChange={(event) => setPhoto(event.target.files?.[0] ?? null)}
                  />
                  {photoPreview && <img className="broadcast-photo-preview" src={photoPreview} alt="Предпросмотр рассылки" />}
                </div>
              )}

              <div className="broadcast-reaction-builder">
                <label className="broadcast-reaction-toggle">
                  <input
                    type="checkbox"
                    checked={reactionsEnabled}
                    onChange={(event) => setReactionsEnabled(event.target.checked)}
                  />
                  Собирать реакции
                </label>
                {reactionsEnabled && (
                  <>
                    <p className="broadcast-note">
                      Будет добавлен блок <code>[reactions]</code>. В админских чатах используются реакции Telegram,
                      в остальных — inline-кнопки.
                    </p>
                    {reactionRows.map((row, index) => (
                      <div className="broadcast-reaction-row" key={index}>
                        <input
                          aria-label={`Emoji реакции ${index + 1}`}
                          value={row.emoji}
                          maxLength={32}
                          onChange={(event) => setReactionRows((current) => current.map((item, itemIndex) => (
                            itemIndex === index ? { ...item, emoji: event.target.value } : item
                          )))}
                        />
                        <input
                          aria-label={`Описание реакции ${index + 1}`}
                          value={row.label}
                          maxLength={64}
                          onChange={(event) => setReactionRows((current) => current.map((item, itemIndex) => (
                            itemIndex === index ? { ...item, label: event.target.value } : item
                          )))}
                        />
                        {reactionRows.length > 2 && (
                          <button type="button" className="button ghost small" onClick={() => setReactionRows((current) => current.filter((_, itemIndex) => itemIndex !== index))}>Удалить</button>
                        )}
                      </div>
                    ))}
                    {reactionRows.length < 6 && (
                      <button type="button" className="button ghost small" onClick={() => setReactionRows((current) => [...current, { emoji: '🤔', label: 'Есть вопросы' }])}>
                        Добавить вариант
                      </button>
                    )}
                  </>
                )}
              </div>

              <div>
                <div className="broadcast-targets-head">
                  <span className="broadcast-label">Чаты для этой отправки</span>
                  <div className="broadcast-target-actions">
                    <button type="button" className="button ghost small" onClick={selectAll}>Выбрать все</button>
                    <button type="button" className="button ghost small" onClick={selectNone}>Снять все</button>
                  </div>
                </div>
                {data.recent_active_chats.length > 0 ? (
                  <div className="broadcast-target-list">
                    {data.recent_active_chats.map((chat) => (
                      <label key={chat.chat_id} className="broadcast-target-item">
                        <input
                          type="checkbox"
                          checked={selectedChatIds.has(chat.chat_id)}
                          onChange={() => toggleChat(chat.chat_id)}
                        />
                        <span className="broadcast-target-copy">
                          <strong>{chat.title}</strong>
                          <small>ID {chat.chat_id} · активность: {chat.last_activity_at}</small>
                        </span>
                      </label>
                    ))}
                  </div>
                ) : (
                  <p className="empty-text">Пока нет чатов, подходящих под фильтр активности.</p>
                )}
              </div>

              <button type="submit" className="button primary" disabled={broadcastMutation.isPending}>
                {broadcastMutation.isPending ? 'Отправляю…' : 'Отправить в активные чаты'}
              </button>
            </form>

            <div className="broadcast-side">
              <article className="broadcast-stat-card">
                <span className="broadcast-stat-label">Охват прямо сейчас</span>
                <strong>{data.recent_active_chat_count}</strong>
                <small>чатов подходят под фильтр активности</small>
              </article>
              <article className="broadcast-stat-card">
                <span className="broadcast-stat-label">Фильтр охвата</span>
                <strong>{data.broadcast_active_days} дня</strong>
                <small>Берутся только чаты с активностью в этот период.</small>
              </article>
              <article className="broadcast-stat-card">
                <span className="broadcast-stat-label">Что сохранится</span>
                <strong>Доставки и reply</strong>
                <small>История отправок и прямые ответы останутся в админке.</small>
              </article>
            </div>
          </div>

          {/* Broadcast history */}
          <div>
            <h4 style={{ margin: '0 0 0.5rem' }}>Последние рассылки</h4>
            <p className="admin-panel__subtitle" style={{ marginBottom: '0.75rem' }}>История доставок и ответов реплаем.</p>
            {data.recent_broadcasts.length > 0 ? (
              <div className="broadcast-history">
                {data.recent_broadcasts.map((item) => (
                  <Link key={item.id} className="broadcast-history-card" to={routes.adminBroadcast(item.id)}>
                    <div className="broadcast-history-head">
                      <strong>#{item.id}</strong>
                      <small>{item.created_at}</small>
                    </div>
                    <p className="broadcast-history-body">{item.body_preview}</p>
                    <div className="broadcast-history-metrics">
                      <span>целей: <code>{item.target_count}</code></span>
                      <span>доставлено: <code>{item.sent_count}</code></span>
                      <span>ошибок: <code>{item.failed_count}</code></span>
                      <span>ответов: <code>{item.reply_count}</code></span>
                    </div>
                  </Link>
                ))}
              </div>
            ) : (
              <p className="empty-text">Рассылок ещё не было.</p>
            )}
          </div>
        </div>

        {/* Feedback section */}
        <div className="admin-section" style={{ marginTop: '2rem' }}>
          <div className="admin-section-head">
            <h3>Обратная связь по функциям</h3>
            <p className="admin-panel__subtitle">Последние заявки пользователей. Открытые идут первыми.</p>
          </div>
          {data.feedback_requests.length > 0 ? (
            <div className="admin-feedback-list">
              {data.feedback_requests.map((item) => (
                <article key={item.id} className="admin-feedback-card">
                  <div className="admin-feedback-card__head">
                    <div>
                      <strong>#{item.id} · {item.title}</strong>
                      <p className="admin-feedback-meta">{item.author_label} · {item.created_at}</p>
                    </div>
                    <span className={`feedback-status feedback-status--${item.status_code}`}>{item.status_label}</span>
                  </div>
                  <p className="admin-feedback-body">{item.details}</p>
                  <div className="admin-feedback-actions">
                    <span className="admin-feedback-note">{item.status_note}</span>
                    <button
                      className={`button ${item.is_done ? 'ghost' : 'primary'}`}
                      disabled={feedbackMutation.isPending}
                      onClick={() =>
                        feedbackMutation.mutate({ id: item.id, status: item.is_done ? 'open' : 'done' })
                      }
                    >
                      {item.is_done ? 'Вернуть в открытые' : 'Отметить как сделано'}
                    </button>
                  </div>
                </article>
              ))}
            </div>
          ) : (
            <p className="empty-text">Пользовательские заявки пока не приходили.</p>
          )}
        </div>

        {/* Table sections */}
        {data.table_sections.map((section) => (
          <div key={section.title} className="admin-section" style={{ marginTop: '2rem' }}>
            <div className="admin-section-head">
              <h3>{section.title}</h3>
              <p className="admin-panel__subtitle">{section.tables.length} таблиц</p>
            </div>
            <div className="admin-tables-grid">
              {section.tables.map(([key, title]) => (
                <Link key={key} className="admin-table-card" to={routes.adminTable(key)}>
                  <span className="admin-table-icon">📊</span>
                  <div className="admin-table-info">
                    <strong>{title}</strong>
                    <small>{key}</small>
                  </div>
                  <span className="admin-table-arrow">→</span>
                </Link>
              ))}
            </div>
          </div>
        ))}

        {/* Actions */}
        <div className="admin-actions" style={{ marginTop: '2rem' }}>
          <button
            className="button"
            disabled={backupMutation.isPending}
            onClick={() => backupMutation.mutate()}
          >
            {backupMutation.isPending ? 'Отправляю…' : 'Запросить бекап'}
          </button>
          <button
            className="button danger"
            disabled={logoutMutation.isPending}
            onClick={() => logoutMutation.mutate()}
          >
            Выйти из админки
          </button>
        </div>
      </article>
    </div>
  )
}
