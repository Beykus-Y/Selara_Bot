import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useParams } from 'react-router-dom'

import type { ChatLeaderboardData, ChatLeaderboardMode } from '@/pages/chat/model/types'
import { routes } from '@/shared/config/routes'
import { usePageTitle } from '@/shared/lib/use-page-title'
import { getMiniAppData, getMiniAppPage } from '@/shared/miniapp/api'
import type { MiniAppChatPageData } from '@/shared/miniapp/model'
import { LoadingShell } from '@/shared/ui/LoadingShell'

function modeLabel(mode: ChatLeaderboardMode) {
  if (mode === 'activity') {
    return 'Activity'
  }

  if (mode === 'karma') {
    return 'Karma'
  }

  return 'Mix'
}

export function ChatPage() {
  const { chatId } = useParams()
  const [mode, setMode] = useState<ChatLeaderboardMode>('mix')
  const [page, setPage] = useState(1)
  const [query, setQuery] = useState('')
  const [searchValue, setSearchValue] = useState('')
  const [findMeRequested, setFindMeRequested] = useState(false)

  const overviewQuery = useQuery({
    queryKey: ['miniapp-chat-overview', chatId],
    queryFn: () => getMiniAppPage<MiniAppChatPageData>(`/miniapp/chat/${chatId}`, 'Не удалось загрузить обзор группы.'),
    enabled: Boolean(chatId),
  })

  const leaderboardQuery = useQuery({
    queryKey: ['miniapp-chat-leaderboard', chatId, mode, page, query, findMeRequested],
    queryFn: () =>
      getMiniAppData<ChatLeaderboardData>(`/miniapp/chat/${chatId}/leaderboard`, 'Не удалось загрузить лидерборд.', {
        params: {
          mode,
          page,
          q: query,
          find_me: findMeRequested ? 1 : 0,
        },
      }),
    enabled: Boolean(chatId),
  })

  usePageTitle(overviewQuery.data?.chat_title || 'Group')

  useEffect(() => {
    if (!findMeRequested || !leaderboardQuery.data) {
      return
    }

    setPage(leaderboardQuery.data.page)
    setFindMeRequested(false)
  }, [findMeRequested, leaderboardQuery.data])

  if (!chatId) {
    return <section className="miniapp-empty-card">Не удалось определить ID чата.</section>
  }

  if (overviewQuery.isLoading || leaderboardQuery.isLoading) {
    return <LoadingShell eyebrow="Group" title="Собираю overview и leaderboard" cards={3} />
  }

  if (overviewQuery.isError) {
    return <section className="miniapp-empty-card">{overviewQuery.error.message}</section>
  }

  if (leaderboardQuery.isError) {
    return <section className="miniapp-empty-card">{leaderboardQuery.error.message}</section>
  }

  if (!overviewQuery.data || !leaderboardQuery.data) {
    return <LoadingShell eyebrow="Group" title="Подгружаю рабочую область" cards={3} />
  }

  const maxMessages = Math.max(...overviewQuery.data.daily_activity.map((item) => item.messages), 1)
  const previewSections = overviewQuery.data.leaderboards.slice(0, 3)
  const summaryItems = [
    {
      label: 'Participants',
      value: String(overviewQuery.data.summary.participants_count),
    },
    {
      label: 'Messages',
      value: String(overviewQuery.data.summary.total_messages),
    },
    {
      label: 'Last seen',
      value: overviewQuery.data.summary.last_activity_at,
    },
  ]

  return (
    <div className="miniapp-page-stack">
      <section className="miniapp-hero-card">
        <span className="miniapp-hero-card__eyebrow">Group</span>
        <div className="miniapp-hero-card__headline">
          <div>
            <h1>{overviewQuery.data.chat_title}</h1>
            <p>{overviewQuery.data.hero_subtitle}</p>
          </div>
          <a className="button button--secondary" href={routes.desktopChat(chatId)} target="_blank" rel="noreferrer">
            ПК-панель
          </a>
        </div>
      </section>

      <section className="miniapp-stat-strip">
        {summaryItems.map((item) => (
          <article key={item.label} className="miniapp-stat-strip__item">
            <span>{item.label}</span>
            <strong>{item.value}</strong>
          </article>
        ))}
      </section>

      <section className="miniapp-chart-card">
        <div className="miniapp-section-head">
          <div>
            <h2>Key metrics</h2>
            <p>Основные сигналы группы и ваш текущий ранг внутри чата.</p>
          </div>
        </div>
        <div className="miniapp-metric-grid">
          {overviewQuery.data.metrics.map((item) => (
            <article key={item.label} className={`miniapp-metric-card miniapp-metric-card--${item.tone}`}>
              <span>{item.label}</span>
              <strong>{item.value}</strong>
              <p>{item.note}</p>
            </article>
          ))}
        </div>
      </section>

      <section className="miniapp-chart-card">
        <div className="miniapp-section-head">
          <div>
            <h2>Daily activity</h2>
            <p>Последние 7 дней активности по сообщениям.</p>
          </div>
        </div>
        <div className="miniapp-chart">
          {overviewQuery.data.daily_activity.map((item) => (
            <div key={item.date} className="miniapp-chart__bar">
              <span>{item.messages}</span>
              <div
                className="miniapp-chart__value"
                style={{ height: `${Math.max(20, (item.messages / maxMessages) * 100)}%` }}
              />
              <span className="miniapp-chart__label">{item.label}</span>
            </div>
          ))}
        </div>
      </section>

      <section className="miniapp-glance-grid">
        <article className="miniapp-glance-card">
          <span>Hero of day</span>
          <strong>{overviewQuery.data.hero_of_day?.label || 'Пока нет лидера'}</strong>
          <p>
            {overviewQuery.data.hero_of_day
              ? `${overviewQuery.data.hero_of_day.messages} сообщений • карма ${overviewQuery.data.hero_of_day.karma}`
              : 'Данные появятся после активности в чате.'}
          </p>
        </article>
        <article className="miniapp-glance-card">
          <span>Richest of day</span>
          <strong>{overviewQuery.data.richest_of_day?.label || 'Экономика не активна'}</strong>
          <p>
            {overviewQuery.data.richest_of_day
              ? `Баланс ${overviewQuery.data.richest_of_day.balance}`
              : 'Локальный richest недоступен для этого чата.'}
          </p>
        </article>
      </section>

      <section className="miniapp-section-card">
        <div className="miniapp-section-head">
          <div>
            <h2>Overview panels</h2>
            <p>Только обзорные блоки без настроек, achievements и audit.</p>
          </div>
        </div>
        <div className="miniapp-list-stack">
          {overviewQuery.data.dashboard_panels.map((panel) => (
            <article key={panel.title} className="miniapp-inline-card">
              <div>
                <strong>{panel.title}</strong>
                <p>{panel.empty_text || `${panel.rows.length} rows`}</p>
              </div>
              <div className="miniapp-inline-card__meta">
                {panel.rows.slice(0, 2).map((row) => (
                  <span key={`${panel.title}-${row.title}`}>
                    {row.title}: {row.value}
                  </span>
                ))}
              </div>
            </article>
          ))}
        </div>
      </section>

      <section className="miniapp-section-card">
        <div className="miniapp-section-head">
          <div>
            <h2>Leaderboard preview</h2>
            <p>Быстрый обзор трёх режимов рейтинга перед полной таблицей.</p>
          </div>
        </div>
        <div className="miniapp-leaderboard-preview">
          {previewSections.map((section) => (
            <article key={section.title} className="miniapp-leaderboard-preview__section">
              <h3>{section.title}</h3>
              <p>{section.subtitle}</p>
              {section.rows.slice(0, 3).map((row) => (
                <div key={`${section.title}-${row.position}`} className="miniapp-leaderboard-row">
                  <strong>{row.position}</strong>
                  <div>
                    <strong>{row.name}</strong>
                    <span>{row.primary}</span>
                    <span>{row.secondary}</span>
                  </div>
                </div>
              ))}
            </article>
          ))}
        </div>
      </section>

      <section className="miniapp-table-card">
        <div className="miniapp-section-head">
          <div>
            <h2>Leaderboard</h2>
            <p>
              {leaderboardQuery.data.truncated
                ? 'Показан урезанный серверный набор записей.'
                : 'Поиск, режимы и пагинация работают прямо внутри miniapp.'}
            </p>
          </div>
        </div>

        <div className="miniapp-mode-switch">
          {(['mix', 'activity', 'karma'] as const).map((item) => (
            <button
              key={item}
              className={item === mode ? 'button button--primary' : 'button button--secondary'}
              type="button"
              onClick={() => {
                setMode(item)
                setPage(1)
                setFindMeRequested(false)
              }}
            >
              {modeLabel(item)}
            </button>
          ))}
        </div>

        <div className="miniapp-table-controls">
          <form
            onSubmit={(event) => {
              event.preventDefault()
              setPage(1)
              setQuery(searchValue.trim())
              setFindMeRequested(false)
            }}
          >
            <input
              value={searchValue}
              placeholder="Поиск по имени или @username"
              onChange={(event) => setSearchValue(event.target.value)}
            />
            <button className="button button--secondary" type="submit">
              Найти
            </button>
          </form>
          <button
            className="button button--secondary"
            type="button"
            onClick={() => {
              setPage(1)
              setFindMeRequested(true)
            }}
          >
            Найти меня
          </button>
        </div>

        <div className="miniapp-table-list">
          {leaderboardQuery.data.rows.map((row) => (
            <article key={`${row.position}-${row.user_id}`} className="miniapp-table-row">
              <strong>{row.position}</strong>
              <div>
                <strong>{row.is_me ? `${row.name} · вы` : row.name}</strong>
                <div className="miniapp-table-row__meta">
                  {row.username ? <span>{row.username}</span> : null}
                  <span>activity {row.activity}</span>
                  <span>karma {row.karma}</span>
                  <span>mix {row.hybrid_score}</span>
                  <span>{row.last_seen_at}</span>
                </div>
              </div>
            </article>
          ))}
        </div>

        <div className="miniapp-table-pagination">
          <button
            className="button button--secondary"
            type="button"
            disabled={leaderboardQuery.data.page <= 1}
            onClick={() => setPage((current) => Math.max(1, current - 1))}
          >
            Назад
          </button>
          <span>
            Страница {leaderboardQuery.data.page} / {leaderboardQuery.data.total_pages}
            {leaderboardQuery.data.my_rank ? ` · ваш ранг ${leaderboardQuery.data.my_rank}` : ''}
          </span>
          <button
            className="button button--secondary"
            type="button"
            disabled={leaderboardQuery.data.page >= leaderboardQuery.data.total_pages}
            onClick={() => setPage((current) => current + 1)}
          >
            Далее
          </button>
        </div>
      </section>
    </div>
  )
}
