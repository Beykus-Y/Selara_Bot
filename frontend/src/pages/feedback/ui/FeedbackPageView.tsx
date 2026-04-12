import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'

import { submitFeedback } from '@/pages/feedback/api/submit-feedback'
import type { FeedbackPageData } from '@/pages/feedback/model/types'

import './feedback-page.css'

type FeedbackPageViewProps = {
  data: FeedbackPageData
}

export function FeedbackPageView({ data }: FeedbackPageViewProps) {
  const [title, setTitle] = useState('')
  const [details, setDetails] = useState('')
  const [flash, setFlash] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const queryClient = useQueryClient()

  const submitMutation = useMutation({
    mutationFn: () => submitFeedback(title, details),
    onSuccess: (result) => {
      setFlash(result.message)
      setError(null)
      setTitle('')
      setDetails('')
      void queryClient.invalidateQueries({ queryKey: ['feedback-page'] })
    },
    onError: (err: Error) => {
      setError(err.message)
      setFlash(null)
    },
  })

  return (
    <div className="feedback-page">
      <section className="feedback-hero">
        <div>
          <span className="page-card__eyebrow">Авторизованная зона</span>
          <h1>Обратная связь по боту</h1>
          <p>Оставьте идеи, чего не хватает в боте.</p>
        </div>
        <div className="feedback-hero__chips">
          <span className="feedback-chip">идеи для бота</span>
          <span className="feedback-chip">только после входа</span>
          <span className="feedback-chip">статус виден в кабинете</span>
        </div>
      </section>

      <div className="feedback-metrics">
        {data.feedback_metrics.map((m) => (
          <article key={m.label} className="feedback-metric-card">
            <span className="feedback-metric-label">{m.label}</span>
            <strong className="feedback-metric-value">{m.value}</strong>
            <span className="feedback-metric-note">{m.note}</span>
          </article>
        ))}
      </div>

      <div className="feedback-grid">
        <article className="feedback-panel feedback-panel--accent">
          <div className="feedback-panel__head">
            <h2>Новая заявка</h2>
            <p className="feedback-panel__subtitle">Коротко назовите функцию и ниже опишите, как она должна работать.</p>
          </div>

          {flash && <div className="feedback-flash">{flash}</div>}
          {error && <div className="feedback-error">{error}</div>}

          <form
            className="feedback-form"
            onSubmit={(e) => {
              e.preventDefault()
              submitMutation.mutate()
            }}
          >
            <label className="feedback-field">
              <span>Что добавить</span>
              <input
                type="text"
                maxLength={160}
                placeholder="Например: напоминания по расписанию"
                required
                value={title}
                onChange={(e) => setTitle(e.target.value)}
              />
            </label>
            <label className="feedback-field">
              <span>Описание</span>
              <textarea
                rows={7}
                placeholder="Что именно должен уметь бот, где это должно появиться и как ты это видишь."
                required
                value={details}
                onChange={(e) => setDetails(e.target.value)}
              />
            </label>
            <button type="submit" className="button primary" disabled={submitMutation.isPending}>
              {submitMutation.isPending ? 'Отправляю…' : 'Отправить идею'}
            </button>
          </form>
        </article>

        <article className="feedback-panel">
          <div className="feedback-panel__head">
            <h2>Как это работает</h2>
            <p className="feedback-panel__subtitle">Эта страница доступна только после обычной авторизации через Telegram.</p>
          </div>
          <div className="feedback-info-list">
            <div className="feedback-info-card">
              <strong>Заявка попадает в админку</strong>
              <p>Там можно просмотреть список, открыть запись через таблицу и быстро менять статус прямо на панели.</p>
            </div>
            <div className="feedback-info-card">
              <strong>Статус виден здесь же</strong>
              <p>Когда админ отметит заявку как сделанную, она останется в истории этого аккаунта со статусом.</p>
            </div>
            <div className="feedback-info-card">
              <strong>Можно отправлять несколько идей</strong>
              <p>Для каждой функции создаётся отдельная запись, так проще отслеживать прогресс по разным запросам.</p>
            </div>
          </div>
        </article>
      </div>

      <section className="feedback-list-panel">
        <div className="feedback-list-head">
          <div>
            <h2>Мои заявки</h2>
            <p className="feedback-panel__subtitle">История отправленных предложений и текущий статус каждой идеи.</p>
          </div>
          <span className="feedback-panel-tag">{data.feedback_items.length}</span>
        </div>

        {data.feedback_items.length > 0 ? (
          <div className="feedback-list">
            {data.feedback_items.map((item) => (
              <article key={item.id} className={`feedback-card feedback-card--${item.status_code}`}>
                <div className="feedback-card__head">
                  <div>
                    <strong>{item.title}</strong>
                    <p className="feedback-card__meta">Создано {item.created_at}</p>
                  </div>
                  <span className={`feedback-status feedback-status--${item.status_code}`}>{item.status_label}</span>
                </div>
                <p className="feedback-card__body">{item.details}</p>
                <p className="feedback-card__note">{item.status_note}</p>
              </article>
            ))}
          </div>
        ) : (
          <p className="feedback-empty">Здесь пока пусто. Первая идея появится после отправки формы выше.</p>
        )}
      </section>
    </div>
  )
}
