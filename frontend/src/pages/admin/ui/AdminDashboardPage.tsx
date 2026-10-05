import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { Link } from 'react-router-dom'

import { routes } from '@/shared/config/routes'
import { usePageTitle } from '@/shared/lib/use-page-title'

import { getAdminAiSummary, getAdminMonetizationSummary } from '../api/get-admin-ai'
import { formatStars, formatUsd } from '../lib/format'
import { getAdminAudience } from '../api/get-admin-audience'
import { getAdminHealth } from '../api/get-admin-health'
import { getAdminSummary } from '../api/get-admin-summary'
import type { AdminHealthComponent, AdminHealthStatus } from '../model/types'

const periods = [
  { days: 1, label: '24 ч' },
  { days: 7, label: '7 дн' },
  { days: 30, label: '30 дн' },
  { days: 90, label: '90 дн' },
]

const statusLabels: Record<AdminHealthStatus, string> = {
  healthy: 'Работает',
  degraded: 'Проблема',
  down: 'Недоступен',
  unknown: 'Нет проверки',
}

function formatCount(value: number) {
  return new Intl.NumberFormat('ru-RU').format(value)
}

function formatChange(value: number | null) {
  if (value === null) return 'недостаточно данных'
  const direction = value > 0 ? '↑' : value < 0 ? '↓' : '→'
  return `${direction} ${Math.abs(value).toLocaleString('ru-RU', { maximumFractionDigits: 1 })}%`
}

function mergeGachaHealth(genshin: AdminHealthComponent, hsr: AdminHealthComponent): AdminHealthComponent {
  const statuses = [genshin.status, hsr.status]
  const status: AdminHealthStatus = statuses.includes('down')
    ? 'degraded'
    : statuses.every((item) => item === 'healthy')
      ? 'healthy'
      : statuses.includes('unknown')
        ? 'unknown'
        : 'degraded'
  const latencies = [genshin.latency_ms, hsr.latency_ms].filter((item): item is number => item !== null)
  return {
    status,
    latency_ms: latencies.length ? Math.max(...latencies) : null,
    checked_at: genshin.checked_at,
    last_success_at: [genshin.last_success_at, hsr.last_success_at].filter((item): item is string => item !== null).sort().at(-1) ?? null,
  }
}

function HealthRows({ components }: { components: Record<string, AdminHealthComponent> }) {
  const gacha = mergeGachaHealth(components.gacha_genshin, components.gacha_hsr)
  const rows = [
    { key: 'telegram_bot', title: 'Telegram Bot', component: components.telegram_bot },
    { key: 'web', title: 'Web', component: components.web },
    { key: 'postgresql', title: 'PostgreSQL', component: components.postgresql },
    { key: 'redis', title: 'Redis', component: components.redis },
    { key: 'gacha', title: 'Gacha', component: gacha },
  ]

  return (
    <ul className="admin-health-list">
      {rows.map(({ key, title, component }) => (
        <li className="admin-health-row" key={key} title={component.last_success_at ? `Последняя успешная проверка: ${new Date(component.last_success_at).toLocaleString('ru-RU')}` : 'Успешной проверки ещё не было'}>
          <span className={`admin-status-dot is-${component.status}`} aria-hidden="true" />
          <span className="admin-health-row__name">{title}</span>
          <span className="admin-health-row__meta">
            {component.latency_ms === null ? statusLabels[component.status] : `${component.latency_ms} мс`}
          </span>
        </li>
      ))}
    </ul>
  )
}

function SectionRetry({ onRetry, busy }: { onRetry: () => void; busy: boolean }) {
  return (
    <button className="admin-retry" type="button" onClick={onRetry} disabled={busy}>
      {busy ? 'Проверяю…' : 'Повторить'}
    </button>
  )
}

function SectionSkeleton({ rows = 2 }: { rows?: number }) {
  return (
    <div className="admin-skeleton-list" aria-label="Загрузка данных">
      {Array.from({ length: rows }, (_, index) => <i key={index} />)}
    </div>
  )
}

export function AdminDashboardPage() {
  const [periodDays, setPeriodDays] = useState(30)
  const health = useQuery({
    queryKey: ['miniapp-admin-health'],
    queryFn: ({ signal }) => getAdminHealth({ signal }),
    staleTime: 30_000,
    refetchInterval: 60_000,
  })
  const audience = useQuery({
    queryKey: ['miniapp-admin-audience', periodDays],
    queryFn: ({ signal }) => getAdminAudience(periodDays, { signal }),
    placeholderData: keepPreviousData,
    staleTime: 30_000,
    refetchInterval: 60_000,
  })
  const summary = useQuery({
    queryKey: ['miniapp-admin-summary'],
    queryFn: ({ signal }) => getAdminSummary({ signal }),
    staleTime: 30_000,
    refetchInterval: 60_000,
  })

  const aiSummary = useQuery({
    queryKey: ['miniapp-admin-ai-summary', 30],
    queryFn: ({ signal }) => getAdminAiSummary(30, { signal }),
    staleTime: 30_000,
    refetchInterval: 60_000,
  })
  const monetization = useQuery({
    queryKey: ['miniapp-admin-monetization-summary', 30],
    queryFn: ({ signal }) => getAdminMonetizationSummary(30, { signal }),
    staleTime: 30_000,
    refetchInterval: 60_000,
  })

  usePageTitle('Selara Admin')

  const metrics = audience.data?.metrics
  const audienceRows: Array<{
    key: string
    label: string
    value: string
    note?: string
    primary?: boolean
    unavailable?: boolean
  }> = metrics ? [
    {
      key: 'active-bot',
      label: 'Активные пользователи бота в личке',
      value: formatCount(metrics.active_bot_users.value),
      note: formatChange(metrics.active_bot_users.change_percent),
      primary: true,
    },
    { key: 'total-bot', label: 'Всего пользователей бота', value: formatCount(metrics.total_bot_users.value) },
    {
      key: 'active-groups',
      label: 'Активные пользователи групп',
      value: formatCount(metrics.active_group_users.value),
      note: formatChange(metrics.active_group_users.change_percent),
    },
    {
      key: 'group-members',
      label: 'Всего участников групп',
      value: metrics.total_group_members.value === null ? '—' : formatCount(metrics.total_group_members.value),
      note: metrics.total_group_members.value === null
        ? `${metrics.total_group_members.checked_groups}/${metrics.total_group_members.total_groups} проверено · недоступно групп: ${metrics.total_group_members.inaccessible_groups} · известных Selara участников: ${formatCount(metrics.total_group_members.known_active_members)}`
        : metrics.total_group_members.inaccessible_groups > 0
          ? `Бот уже не состоит: ${metrics.total_group_members.inaccessible_groups} групп`
          : undefined,
      unavailable: metrics.total_group_members.value === null,
    },
  ] : []

  return (
    <div className="admin-dashboard">
      <section className="admin-dashboard__heading">
        <div>
          <p className="admin-eyebrow">Обзор системы</p>
          <h1>Состояние Selara</h1>
          <p className="admin-subtitle">
            {health.data
              ? `${health.data.environment}${health.data.version ? ` · ${health.data.version.slice(0, 8)}` : ''} · uptime ${Math.floor(health.data.process_uptime_seconds / 3600)} ч`
              : 'Проверка компонентов'}
          </p>
        </div>
        {health.data && (
          <span className={`admin-overall is-${health.data.status}`}>
            <i className={`admin-status-dot is-${health.data.status}`} />
            {health.data.status === 'healthy' ? 'Всё работает' : health.data.status === 'down' ? 'Компонент недоступен' : health.data.status === 'degraded' ? 'Работает с проблемами' : 'Есть непроверенные компоненты'}
          </span>
        )}
      </section>

      <section className="admin-section" aria-labelledby="admin-health-title">
        <div className="admin-section__title-row">
          <h2 id="admin-health-title">Компоненты</h2>
          <SectionRetry onRetry={() => void health.refetch()} busy={health.isFetching} />
        </div>
        {health.isPending ? <SectionSkeleton rows={5} /> : health.isError && !health.data ? (
          <div className="admin-inline-error" role="alert">
            <span>{health.error.message}</span>
            <SectionRetry onRetry={() => void health.refetch()} busy={health.isFetching} />
          </div>
        ) : health.data ? (
          <>
            <HealthRows components={health.data.components} />
            {health.data.components.telegram_bot.detail && (
              <p className="admin-footnote">Статус Telegram Bot не проверяется и оставлен неизвестным.</p>
            )}
            {health.isError && <p className="admin-footnote">Не удалось обновить. Показана последняя проверка.</p>}
          </>
        ) : null}
      </section>

      <section className="admin-section" aria-labelledby="admin-audience-title">
        <div className="admin-section__title-row admin-section__title-row--stack">
          <div className="admin-section__title-row">
            <h2 id="admin-audience-title">Аудитория</h2>
            <SectionRetry onRetry={() => void audience.refetch()} busy={audience.isFetching} />
          </div>
          <div className="admin-periods" aria-label="Период активной аудитории">
            {periods.map((period) => (
              <button
                aria-pressed={periodDays === period.days}
                className={periodDays === period.days ? 'is-selected' : ''}
                key={period.days}
                onClick={() => setPeriodDays(period.days)}
                type="button"
              >
                {period.label}
              </button>
            ))}
          </div>
        </div>
        {audience.isPending ? <SectionSkeleton rows={4} /> : audience.isError && !audience.data ? (
          <div className="admin-inline-error" role="alert">
            <span>{audience.error.message}</span>
            <SectionRetry onRetry={() => void audience.refetch()} busy={audience.isFetching} />
          </div>
        ) : audience.data ? (
          <>
            <div className="admin-metric-grid" aria-busy={audience.isFetching}>
              {audienceRows.map((metric) => (
                <article className={metric.primary ? 'admin-metric is-primary' : 'admin-metric'} key={metric.key}>
                  <strong className={metric.unavailable ? 'is-unavailable' : ''}>{metric.value}</strong>
                  <span>{metric.label}</span>
                  {metric.note && <small>{metric.note}</small>}
                </article>
              ))}
            </div>
            <p className="admin-footnote">
              Активность считается по уникальным Telegram ID. Точный итог участников групп появляется после успешных Telegram snapshots для всех групп.
            </p>
            {audience.isError && <p className="admin-footnote">Не удалось обновить. Показаны последние данные.</p>}
          </>
        ) : null}
      </section>

      <section className="admin-section" aria-labelledby="admin-operations-title">
        <div className="admin-section__title-row">
          <h2 id="admin-operations-title">Оперативная сводка</h2>
          <SectionRetry onRetry={() => void summary.refetch()} busy={summary.isFetching} />
        </div>
        {summary.isPending ? <SectionSkeleton rows={2} /> : summary.isError && !summary.data ? (
          <div className="admin-inline-error" role="alert">Не удалось загрузить сводку. <SectionRetry onRetry={() => void summary.refetch()} busy={summary.isFetching} /></div>
        ) : summary.data ? (
          <>
            <div className="admin-summary-counts">
              <div><strong>{summary.data.errors_24h}</strong><span>оперативных ошибок за 24 ч</span></div>
              <div><strong>{summary.data.open_feedback}</strong><span>открытых обращений · {summary.data.new_feedback_24h} новых</span></div>
            </div>
            {summary.data.recent_events.length ? (
              <ul className="admin-recent-events">
                {summary.data.recent_events.map((event) => (
                  <li key={`${event.source}-${event.id}`}><span className={`admin-status-dot ${event.severity === 'error' ? 'is-down' : 'is-unknown'}`} /><span><strong>{event.message}</strong><small>{event.source} · {new Date(event.created_at).toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' })}</small></span></li>
                ))}
              </ul>
            ) : <p className="admin-footnote">Сохранённых operational ошибок нет.</p>}
            {summary.isError && <p className="admin-footnote">Не удалось обновить. Показаны последние данные.</p>}
          </>
        ) : null}
      </section>
      <section className="admin-section" aria-labelledby="admin-ai-title">
        <div className="admin-section__title-row">
          <h2 id="admin-ai-title">AI и Selara AI · 30 дней</h2>
          <SectionRetry
            onRetry={() => { void aiSummary.refetch(); void monetization.refetch() }}
            busy={aiSummary.isFetching || monetization.isFetching}
          />
        </div>
        <div className="admin-metric-grid">
          {aiSummary.data ? (
            <>
              <article className="admin-metric"><strong>{formatCount(aiSummary.data.invocations)}</strong><span>AI-запросов</span></article>
              <article className="admin-metric"><strong>{formatCount(aiSummary.data.provider_calls)}</strong><span>вызовов провайдера</span></article>
              <article className={aiSummary.data.unknown_cost_calls > 0 ? 'admin-metric is-warn' : 'admin-metric'}>
                <strong>{formatUsd(aiSummary.data.known_cost_usd)}</strong>
                <span>{aiSummary.data.unknown_cost_calls > 0 ? 'известная стоимость' : 'стоимость'}</span>
                {aiSummary.data.unknown_cost_calls > 0 && <small>без цены: {formatCount(aiSummary.data.unknown_cost_calls)} вызовов</small>}
              </article>
            </>
          ) : aiSummary.isError ? (
            <div className="admin-inline-error" role="alert"><span>AI-аналитика недоступна.</span></div>
          ) : <SectionSkeleton rows={2} />}
          {monetization.data ? (
            <>
              <article className="admin-metric"><strong>{formatCount(monetization.data.active_paid_chats)}</strong><span>активных чатов Selara AI</span></article>
              <article className="admin-metric"><strong>{formatStars(monetization.data.stars_revenue)}</strong><span>выручка Stars</span></article>
            </>
          ) : monetization.isError ? (
            <div className="admin-inline-error" role="alert"><span>Аналитика Stars недоступна.</span></div>
          ) : null}
        </div>
        <Link className="admin-dashboard-action" to={routes.adminAi}>Открыть аналитику <span aria-hidden="true">›</span></Link>
      </section>
      <Link className="admin-dashboard-action" to={routes.adminBroadcast}>Создать рассылку <span aria-hidden="true">›</span></Link>
    </div>
  )
}
