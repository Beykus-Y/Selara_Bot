import { keepPreviousData, useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'

import { usePageTitle } from '@/shared/lib/use-page-title'

import { getAdminFeedback, getAdminFeedbackDetail, setAdminFeedbackStatus } from '../api/admin-workflows'

function dateLabel(value: string) {
  return new Intl.DateTimeFormat('ru-RU', { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value))
}

export function AdminFeedbackPage() {
  const queryClient = useQueryClient()
  const [status, setStatus] = useState('open')
  const [search, setSearch] = useState('')
  const [debouncedSearch, setDebouncedSearch] = useState('')
  const [startAt, setStartAt] = useState('')
  const [endAt, setEndAt] = useState('')
  const [selectedId, setSelectedId] = useState<number | null>(null)
  usePageTitle('Feedback · Selara Admin')

  useEffect(() => {
    const timer = window.setTimeout(() => setDebouncedSearch(search.trim()), 250)
    return () => window.clearTimeout(timer)
  }, [search])

  const feedback = useInfiniteQuery({
    queryKey: ['miniapp-admin-feedback', status, debouncedSearch, startAt, endAt],
    initialPageParam: null as number | null,
    queryFn: ({ signal, pageParam }) => getAdminFeedback(
      { status, search: debouncedSearch, limit: 30, ...(startAt ? { start_at: new Date(startAt).toISOString() } : {}), ...(endAt ? { end_at: new Date(endAt).toISOString() } : {}), ...(pageParam ? { before_id: pageParam } : {}) },
      { signal },
    ),
    getNextPageParam: (lastPage) => lastPage.next_cursor ?? undefined,
    placeholderData: keepPreviousData,
    staleTime: 30_000,
  })
  const detail = useQuery({
    queryKey: ['miniapp-admin-feedback-detail', selectedId],
    queryFn: ({ signal }) => getAdminFeedbackDetail(selectedId!, { signal }),
    enabled: selectedId !== null,
    staleTime: 60_000,
  })
  const updateStatus = useMutation({
    mutationFn: ({ id, next }: { id: number; next: 'resolve' | 'reopen' }) => setAdminFeedbackStatus(id, next),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['miniapp-admin-feedback'] })
      await queryClient.invalidateQueries({ queryKey: ['miniapp-admin-feedback-detail', selectedId] })
    },
  })

  return (
    <section className="admin-page">
      <header className="admin-page__heading">
        <p className="admin-eyebrow">Входящие обращения</p>
        <h1>Feedback</h1>
      </header>
      <div className="admin-filter-row" role="group" aria-label="Статус обращения">
        {[
          ['open', 'Открытые'],
          ['resolved', 'Закрытые'],
          ['all', 'Все'],
        ].map(([key, label]) => (
          <button key={key} type="button" className={status === key ? 'is-selected' : ''} onClick={() => setStatus(key)}>
            {label}
          </button>
        ))}
      </div>
      <label className="admin-search">
        <span className="sr-only">Поиск по теме, username или Telegram ID</span>
        <input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Поиск: тема, username, ID" />
      </label>
      <div className="admin-date-filters"><label>С <input type="datetime-local" value={startAt} onChange={(event) => setStartAt(event.target.value)} /></label><label>По <input type="datetime-local" value={endAt} onChange={(event) => setEndAt(event.target.value)} /></label></div>
      {feedback.isPending ? <div className="admin-skeleton-list"><i /><i /><i /></div> : null}
      {feedback.isError ? <div className="admin-inline-error">Не удалось загрузить обращения. <button type="button" onClick={() => void feedback.refetch()}>Повторить</button></div> : null}
      {!feedback.isPending && !feedback.isError && feedback.data?.pages.every((page) => page.items.length === 0) ? <p className="admin-empty">Обращений нет.</p> : null}
      <ul className="admin-inbox-list">
        {feedback.data?.pages.flatMap((page) => page.items).map((item) => (
          <li key={item.id}>
            <button className="admin-inbox-item" type="button" onClick={() => setSelectedId(item.id)}>
              <span className={`admin-status-dot ${item.status === 'open' ? 'is-degraded' : 'is-healthy'}`} />
              <span className="admin-inbox-item__body">
                <strong>#{item.id} · {item.title}</strong>
                <span>{item.preview}</span>
                <small>{item.user.username ? `@${item.user.username}` : item.user.first_name || 'Пользователь'} · {dateLabel(item.created_at)}</small>
              </span>
              <span aria-hidden="true">›</span>
            </button>
          </li>
        ))}
      </ul>
      {feedback.hasNextPage ? <button className="admin-load-more" type="button" disabled={feedback.isFetchingNextPage} onClick={() => void feedback.fetchNextPage()}>{feedback.isFetchingNextPage ? 'Загружаю…' : 'Загрузить ещё'}</button> : null}
      {selectedId !== null ? (
        <div className="admin-sheet-backdrop" role="presentation" onClick={() => setSelectedId(null)}>
          <section className="admin-sheet" role="dialog" aria-modal="true" aria-label="Обращение" onClick={(event) => event.stopPropagation()}>
            <button className="admin-sheet__close" type="button" onClick={() => setSelectedId(null)}>Закрыть</button>
            {detail.isPending ? <div className="admin-skeleton-list"><i /><i /></div> : null}
            {detail.isError ? <div className="admin-inline-error">Не удалось открыть обращение. <button type="button" onClick={() => void detail.refetch()}>Повторить</button></div> : null}
            {detail.data ? (
              <>
                <p className="admin-eyebrow">#{detail.data.id} · {detail.data.status === 'open' ? 'Открыто' : 'Закрыто'}</p>
                <h2>{detail.data.title}</h2>
                <p className="admin-sheet__meta">{detail.data.user.username ? `@${detail.data.user.username}` : detail.data.user.first_name || 'Пользователь'} · Telegram ID {detail.data.user.id}</p>
                <p className="admin-sheet__date">{dateLabel(detail.data.created_at)}</p>
                <div className="admin-feedback-text">{detail.data.details}</div>
                <button
                  type="button"
                  className="admin-primary-action"
                  disabled={updateStatus.isPending}
                  onClick={() => updateStatus.mutate({ id: detail.data!.id, next: detail.data!.status === 'open' ? 'resolve' : 'reopen' })}
                >
                  {updateStatus.isPending ? 'Сохраняю…' : detail.data.status === 'open' ? 'Закрыть обращение' : 'Вернуть в открытые'}
                </button>
              </>
            ) : null}
          </section>
        </div>
      ) : null}
    </section>
  )
}
