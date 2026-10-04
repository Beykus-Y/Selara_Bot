import { keepPreviousData, useInfiniteQuery, useQuery } from '@tanstack/react-query'
import { useEffect, useState } from 'react'

import { usePageTitle } from '@/shared/lib/use-page-title'

import { getAdminAlertDetail, getAdminAlerts, getAdminLogs } from '../api/admin-workflows'

function dateLabel(value: string) {
  return new Intl.DateTimeFormat('ru-RU', { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value))
}

export function AdminMonitoringPage() {
  const [tab, setTab] = useState<'alerts' | 'logs'>('alerts')
  const [severity, setSeverity] = useState('all')
  const [logLevel, setLogLevel] = useState('all')
  const [source, setSource] = useState('')
  const [search, setSearch] = useState('')
  const [debouncedSearch, setDebouncedSearch] = useState('')
  const [startAt, setStartAt] = useState('')
  const [endAt, setEndAt] = useState('')
  const [selectedAlert, setSelectedAlert] = useState<number | null>(null)
  usePageTitle('Мониторинг · Selara Admin')

  useEffect(() => {
    const timer = window.setTimeout(() => setDebouncedSearch(search.trim()), 250)
    return () => window.clearTimeout(timer)
  }, [search])

  const alerts = useInfiniteQuery({
    queryKey: ['miniapp-admin-alerts', severity, source, debouncedSearch, startAt, endAt],
    initialPageParam: null as number | null,
    queryFn: ({ signal, pageParam }) => getAdminAlerts(
      { severity, source, search: debouncedSearch, limit: 30, ...(startAt ? { start_at: new Date(startAt).toISOString() } : {}), ...(endAt ? { end_at: new Date(endAt).toISOString() } : {}), ...(pageParam ? { before_id: pageParam } : {}) },
      { signal },
    ),
    getNextPageParam: (lastPage) => lastPage.next_cursor ?? undefined,
    placeholderData: keepPreviousData,
    enabled: tab === 'alerts',
    staleTime: 30_000,
  })
  const logs = useInfiniteQuery({
    queryKey: ['miniapp-admin-logs', logLevel, source, debouncedSearch, startAt, endAt],
    initialPageParam: null as number | null,
    queryFn: ({ signal, pageParam }) => getAdminLogs(
      { level: logLevel, source, search: debouncedSearch, limit: 30, ...(startAt ? { start_at: new Date(startAt).toISOString() } : {}), ...(endAt ? { end_at: new Date(endAt).toISOString() } : {}), ...(pageParam ? { before_id: pageParam } : {}) },
      { signal },
    ),
    getNextPageParam: (lastPage) => lastPage.next_cursor ?? undefined,
    placeholderData: keepPreviousData,
    enabled: tab === 'logs',
    staleTime: 30_000,
  })
  const alertDetail = useQuery({
    queryKey: ['miniapp-admin-alert-detail', selectedAlert],
    queryFn: ({ signal }) => getAdminAlertDetail(selectedAlert!, { signal }),
    enabled: selectedAlert !== null,
    staleTime: 60_000,
  })
  const currentQuery = tab === 'alerts' ? alerts : logs
  const items = tab === 'alerts'
    ? alerts.data?.pages.flatMap((page) => page.items) ?? []
    : logs.data?.pages.flatMap((page) => page.items) ?? []

  return (
    <section className="admin-page">
      <header className="admin-page__heading">
        <p className="admin-eyebrow">События и диагностика</p>
        <h1>Мониторинг</h1>
      </header>
      <div className="admin-filter-row" role="group" aria-label="Тип журнала">
        <button type="button" className={tab === 'alerts' ? 'is-selected' : ''} onClick={() => setTab('alerts')}>Alerts</button>
        <button type="button" className={tab === 'logs' ? 'is-selected' : ''} onClick={() => setTab('logs')}>Логи</button>
      </div>
      {tab === 'alerts' ? (
        <div className="admin-filter-row" role="group" aria-label="Уровень события">
          {[
            ['all', 'Все'],
            ['error', 'Ошибка'],
            ['warning', 'Warning'],
          ].map(([key, label]) => <button key={key} type="button" className={severity === key ? 'is-selected' : ''} onClick={() => setSeverity(key)}>{label}</button>)}
        </div>
      ) : null}
      {tab === 'logs' ? (
        <div className="admin-filter-row" role="group" aria-label="Уровень журнала">
          {[
            ['all', 'Все'],
            ['info', 'Info'],
            ['warning', 'Warning'],
            ['error', 'Error'],
          ].map(([key, label]) => <button key={key} type="button" className={logLevel === key ? 'is-selected' : ''} onClick={() => setLogLevel(key)}>{label}</button>)}
        </div>
      ) : null}
      <div className="admin-monitor-filters">
        <input value={source} onChange={(event) => setSource(event.target.value)} placeholder="Модуль / source" aria-label="Фильтр по модулю" />
        <input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Поиск по событиям" aria-label="Поиск по событиям" />
        <label><span>С</span><input type="datetime-local" value={startAt} onChange={(event) => setStartAt(event.target.value)} aria-label="Начало периода" /></label>
        <label><span>По</span><input type="datetime-local" value={endAt} onChange={(event) => setEndAt(event.target.value)} aria-label="Конец периода" /></label>
      </div>
      {tab === 'logs' ? <p className="admin-footnote">Журнал процесса: последние записи уровня INFO и выше (до 1&nbsp;000). Историю systemd и полный stdout запрос не читает.</p> : null}
      {currentQuery.isPending ? <div className="admin-skeleton-list"><i /><i /><i /></div> : null}
      {currentQuery.isError ? <div className="admin-inline-error">Раздел временно недоступен. <button type="button" onClick={() => void currentQuery.refetch()}>Повторить</button></div> : null}
      {!currentQuery.isPending && !currentQuery.isError && items.length === 0 ? <p className="admin-empty">Событий нет.</p> : null}
      <ul className="admin-event-list">
        {items.map((item) => (
          <li key={item.id}>
            <button className="admin-event-item" type="button" onClick={() => 'severity' in item && setSelectedAlert(item.id)}>
              <span className={`admin-status-dot ${'severity' in item ? 'is-down' : 'is-unknown'}`} aria-hidden="true" />
              <span><strong>{item.message}</strong><small>{item.source} · {dateLabel(item.created_at)}</small></span>
              {'fingerprint' in item ? <code>{item.fingerprint}</code> : null}
            </button>
          </li>
        ))}
      </ul>
      {currentQuery.hasNextPage ? <button className="admin-load-more" type="button" disabled={currentQuery.isFetchingNextPage} onClick={() => void currentQuery.fetchNextPage()}>{currentQuery.isFetchingNextPage ? 'Загружаю…' : 'Загрузить ещё'}</button> : null}
      {selectedAlert !== null ? (
        <div className="admin-sheet-backdrop" role="presentation" onClick={() => setSelectedAlert(null)}>
          <section className="admin-sheet" role="dialog" aria-modal="true" aria-label="Детали operational alert" onClick={(event) => event.stopPropagation()}>
            <button className="admin-sheet__close" type="button" onClick={() => setSelectedAlert(null)}>Закрыть</button>
            {alertDetail.isPending ? <div className="admin-skeleton-list"><i /><i /></div> : null}
            {alertDetail.isError ? <div className="admin-inline-error">Не удалось загрузить событие. <button type="button" onClick={() => void alertDetail.refetch()}>Повторить</button></div> : null}
            {alertDetail.data ? (
              <>
                <p className="admin-eyebrow">Operational alert · {dateLabel(alertDetail.data.created_at)}</p>
                <h2>{alertDetail.data.message}</h2>
                <p className="admin-sheet__meta">{alertDetail.data.source} · fingerprint {alertDetail.data.fingerprint}</p>
                {alertDetail.data.context ? <pre className="admin-context">{JSON.stringify(alertDetail.data.context, null, 2)}</pre> : null}
                <div className="admin-trace-heading">
                  <strong>Sanitized traceback</strong>
                  <button type="button" onClick={() => void navigator.clipboard?.writeText(alertDetail.data?.traceback ?? '')}>Копировать</button>
                </div>
                <pre className="admin-trace">{alertDetail.data.traceback || 'Traceback отсутствует.'}</pre>
              </>
            ) : null}
          </section>
        </div>
      ) : null}
    </section>
  )
}
