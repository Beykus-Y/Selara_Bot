import { keepPreviousData, useInfiniteQuery, useQuery } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'

import { routes } from '@/shared/config/routes'
import { usePageTitle } from '@/shared/lib/use-page-title'

import {
  getAdminAiBreakdown,
  getAdminAiReadiness,
  getAdminAiSummary,
  getAdminEntitlements,
  getAdminMonetizationSummary,
  getAdminPaymentDetail,
  getAdminPayments,
} from '../api/get-admin-ai'
import type { AdminPayment, AdminPaymentFilters } from '../api/get-admin-ai'
import {
  featureLabel,
  formatCount,
  formatDateTime,
  formatDay,
  formatStars,
  formatUsd,
  refundLabels,
} from '../lib/format'
import { AdminModelsSection } from './AdminModelsSection'
import { AdminFeatureRoutesSection } from './AdminFeatureRoutesSection'
import { AdminQuotaModeSection } from './AdminQuotaModeSection'
import { MiniBars, Metric, SectionError, SectionRetry, SectionSkeleton } from './AdminAiParts'

const periods = [
  { days: 1, label: '24 ч' },
  { days: 7, label: '7 дн' },
  { days: 30, label: '30 дн' },
  { days: 90, label: '90 дн' },
]

const defaultFilters: AdminPaymentFilters = { state: 'all', refund: 'all', chatId: '', buyerId: '', periodDays: null }

function PeriodSwitch({ value, onChange }: { value: number; onChange: (days: number) => void }) {
  return (
    <div className="admin-periods" role="group" aria-label="Период аналитики">
      {periods.map((period) => (
        <button
          aria-pressed={value === period.days}
          className={value === period.days ? 'is-selected' : ''}
          key={period.days}
          onClick={() => onChange(period.days)}
          type="button"
        >
          {period.label}
        </button>
      ))}
    </div>
  )
}

function ReadinessSection() {
  const readiness = useQuery({
    queryKey: ['miniapp-admin-ai-readiness'],
    queryFn: ({ signal }) => getAdminAiReadiness({ signal }),
    staleTime: 60_000,
  })
  const statusText = { ok: 'Готово', unavailable: 'Недоступно', missing: 'Не настроено' } as const
  return (
    <section className="admin-section" aria-labelledby="ai-readiness-title">
      <div className="admin-section__title-row">
        <h2 id="ai-readiness-title">Готовность Selara AI</h2>
        <SectionRetry onRetry={() => void readiness.refetch()} busy={readiness.isFetching} />
      </div>
      {readiness.isPending ? <SectionSkeleton rows={4} /> : readiness.isError && !readiness.data ? (
        <SectionError message={readiness.error.message} onRetry={() => void readiness.refetch()} busy={readiness.isFetching} />
      ) : readiness.data ? (
        <>
          <ul className="admin-check-list">
            {readiness.data.checks.map((check) => (
              <li className={`is-${check.status}`} key={check.key}>
                <span className="admin-check-list__state">{check.status === 'ok' ? '✓' : '!'} {statusText[check.status]}</span>
                <strong>{check.label}</strong>
                {check.detail && <small>{check.detail}</small>}
              </li>
            ))}
          </ul>
          <p className="admin-footnote">Только диагностика: тестовых покупок и вызовов провайдера не выполняется.</p>
        </>
      ) : null}
    </section>
  )
}

function AiSummarySection({ periodDays }: { periodDays: number }) {
  const summary = useQuery({
    queryKey: ['miniapp-admin-ai-summary', periodDays],
    queryFn: ({ signal }) => getAdminAiSummary(periodDays, { signal }),
    placeholderData: keepPreviousData,
    staleTime: 30_000,
  })
  const data = summary.data
  const unknown = data?.unknown_cost_calls ?? 0
  const average = data?.average_known_cost_per_invocation_usd ?? null
  return (
    <section className="admin-section" aria-labelledby="ai-usage-title">
      <div className="admin-section__title-row">
        <h2 id="ai-usage-title">AI-расходы</h2>
        <SectionRetry onRetry={() => void summary.refetch()} busy={summary.isFetching} />
      </div>
      {summary.isPending ? <SectionSkeleton rows={4} /> : summary.isError && !data ? (
        <SectionError message={summary.error.message} onRetry={() => void summary.refetch()} busy={summary.isFetching} />
      ) : data ? (
        <>
          {data.invocations === 0 && data.provider_calls === 0 ? (
            <p className="admin-empty">Пока нет AI-запросов за этот период.</p>
          ) : null}
          <div className="admin-metric-grid" aria-busy={summary.isFetching}>
            <Metric label="AI-запросов" value={formatCount(data.invocations)} />
            <Metric label="Вызовов провайдера" value={formatCount(data.provider_calls)} />
            <Metric label="Неуспешные запросы" value={formatCount(data.unsuccessful_invocations)} note="ошибка или частичный результат" />
            <Metric
              label={unknown > 0 ? 'Известная стоимость' : 'Стоимость'}
              value={formatUsd(data.known_cost_usd)}
              tone={unknown > 0 ? 'warn' : undefined}
            />
          </div>
          {unknown > 0 && (
            <p className="admin-warning" role="note">
              Есть {formatCount(unknown)} вызовов с неизвестной стоимостью; фактический расход выше или равен указанному.
            </p>
          )}
          {average !== null && data.invocations > 0 && (
            <p className="admin-footnote">
              {unknown > 0 ? 'Средняя известная часть стоимости' : 'Средняя стоимость запроса'}: {formatUsd(average)}
            </p>
          )}
          {data.daily.length > 1 && (
            <MiniBars
              title="Известная стоимость по дням (USD)"
              points={data.daily.map((day) => ({ key: day.date, label: formatDay(day.date), value: Number(day.known_cost_usd) }))}
              format={(value) => formatUsd(String(value))}
            />
          )}
          {summary.isError && <p className="admin-footnote">Не удалось обновить. Показаны последние данные.</p>}
        </>
      ) : null}
    </section>
  )
}

function BreakdownSection({ periodDays }: { periodDays: number }) {
  const breakdown = useQuery({
    queryKey: ['miniapp-admin-ai-breakdown', periodDays],
    queryFn: ({ signal }) => getAdminAiBreakdown(periodDays, { signal }),
    placeholderData: keepPreviousData,
    staleTime: 30_000,
  })
  const data = breakdown.data
  return (
    <section className="admin-section" aria-labelledby="ai-breakdown-title">
      <div className="admin-section__title-row">
        <h2 id="ai-breakdown-title">Разбивка расходов</h2>
        <SectionRetry onRetry={() => void breakdown.refetch()} busy={breakdown.isFetching} />
      </div>
      {breakdown.isPending ? <SectionSkeleton rows={3} /> : breakdown.isError && !data ? (
        <SectionError message={breakdown.error.message} onRetry={() => void breakdown.refetch()} busy={breakdown.isFetching} />
      ) : data ? (
        <div aria-busy={breakdown.isFetching}>
          <h3 className="admin-subheading">По функциям</h3>
          {data.features.length === 0 ? <p className="admin-empty">Пока нет AI-запросов за этот период.</p> : (
            <ul className="admin-rows">
              {data.features.map((row) => (
                <li key={row.feature}>
                  <div className="admin-rows__main">
                    <strong>{featureLabel(row.feature)}</strong>
                    <span>{formatUsd(row.known_cost_usd)}</span>
                  </div>
                  <small>
                    {formatCount(row.invocations)} запросов · {formatCount(row.provider_calls)} вызовов
                    {row.unsuccessful_invocations > 0 ? ` · неуспешных: ${formatCount(row.unsuccessful_invocations)}` : ''}
                    {row.unknown_cost_calls > 0 ? ` · без цены: ${formatCount(row.unknown_cost_calls)}` : ''}
                  </small>
                </li>
              ))}
            </ul>
          )}
          <h3 className="admin-subheading">По моделям</h3>
          {data.models.length === 0 ? <p className="admin-empty">Использованных моделей нет.</p> : (
            <ul className="admin-rows">
              {data.models.map((row) => (
                <li key={row.model}>
                  <div className="admin-rows__main">
                    <strong className="admin-rows__name">{row.model}</strong>
                    <span>{formatUsd(row.known_cost_usd)}</span>
                  </div>
                  <small>
                    {formatCount(row.provider_calls)} вызовов · {formatCount(row.prompt_tokens)} prompt · {formatCount(row.completion_tokens)} completion tokens
                    {row.unknown_cost_calls > 0 ? ` · без цены: ${formatCount(row.unknown_cost_calls)}` : ''}
                  </small>
                </li>
              ))}
            </ul>
          )}
          <h3 className="admin-subheading">AI Limits по профилям</h3>
          <p className="admin-footnote">AIL — продуктовая единица пользователя (реально зарезервированные units), USD — себестоимость провайдера. Это разные величины.</p>
          {(data.ail_profiles ?? []).length === 0 ? <p className="admin-empty">AIL за период не расходовались.</p> : (
            <ul className="admin-rows">
              <li><div className="admin-rows__main"><strong>Всего</strong><span>{data.ail_consumed} AIL</span></div></li>
              {(data.ail_profiles ?? []).map((row) => {
                const usd = (data.profiles ?? []).find((item) => item.profile_key === row.profile_key)
                return (
                  <li key={row.profile_key ?? 'none'}>
                    <div className="admin-rows__main">
                      <strong className="admin-rows__name">{row.profile_key ?? 'без профиля'}</strong>
                      <span>{row.ail_consumed} AIL</span>
                    </div>
                    <small>
                      {formatCount(row.requests)} запросов · известная стоимость {formatUsd(usd?.known_cost_usd ?? '0')}
                    </small>
                  </li>
                )
              })}
            </ul>
          )}
          {data.unattributed_provider_calls > 0 && (
            <p className="admin-footnote">
              Ещё {formatCount(data.unattributed_provider_calls)} попыток провайдера начаты без записи использования (модель неизвестна, стоимость неизвестна).
            </p>
          )}
          {data.stages.length > 0 && (
            <details className="admin-details">
              <summary>Топ этапов</summary>
              <ul className="admin-rows">
                {data.stages.map((row) => (
                  <li key={`${row.feature}-${row.stage}`}>
                    <div className="admin-rows__main">
                      <strong>{featureLabel(row.feature)} · {row.stage}</strong>
                      <span>{formatUsd(row.known_cost_usd)}</span>
                    </div>
                    <small>{formatCount(row.provider_calls)} вызовов</small>
                  </li>
                ))}
              </ul>
            </details>
          )}
          {breakdown.isError && <p className="admin-footnote">Не удалось обновить. Показаны последние данные.</p>}
        </div>
      ) : null}
    </section>
  )
}

function StarsSection({ periodDays }: { periodDays: number }) {
  const summary = useQuery({
    queryKey: ['miniapp-admin-monetization-summary', periodDays],
    queryFn: ({ signal }) => getAdminMonetizationSummary(periodDays, { signal }),
    placeholderData: keepPreviousData,
    staleTime: 30_000,
  })
  const data = summary.data
  return (
    <section className="admin-section" aria-labelledby="ai-stars-title">
      <div className="admin-section__title-row">
        <h2 id="ai-stars-title">Telegram Stars</h2>
        <SectionRetry onRetry={() => void summary.refetch()} busy={summary.isFetching} />
      </div>
      {summary.isPending ? <SectionSkeleton rows={4} /> : summary.isError && !data ? (
        <SectionError message={summary.error.message} onRetry={() => void summary.refetch()} busy={summary.isFetching} />
      ) : data ? (
        <>
          {data.successful_payments === 0 && data.rejected_payments === 0 && (
            <p className="admin-empty">Платежей Selara AI пока нет.</p>
          )}
          <div className="admin-metric-grid" aria-busy={summary.isFetching}>
            <Metric label="Выручка Stars" value={formatStars(data.stars_revenue)} note={`всего: ${formatStars(data.all_time.stars_revenue)}`} />
            <Metric label="Успешных платежей" value={formatCount(data.successful_payments)} />
            <Metric label="Активных платных чатов" value={formatCount(data.active_paid_chats)} note={`истекают за 7 дн: ${formatCount(data.expiring_within_7_days)}`} />
            <Metric label="Активных Personal" value={formatCount(data.active_personal_subscriptions ?? 0)} note={`истекают за 7 дн: ${formatCount(data.personal_expiring_within_7_days ?? 0)}`} />
            <Metric label="Отклонённых платежей" value={formatCount(data.rejected_payments)} tone={data.rejected_payments > 0 ? 'warn' : undefined} />
          </div>
          <p className="admin-footnote">
            Возвраты отклонённых: в процессе {data.refunds.pending} · выполнено {data.refunds.refunded} · не удалось {data.refunds.failed}.
          </p>
          <p className="admin-footnote">
            {data.checkout.configured
              ? `Цена Selara AI: ${formatStars(data.checkout.price_stars ?? 0)}.`
              : data.checkout.price_stars === null
                ? 'Checkout выключен: SELARA_AI_PRICE_STARS не настроен.'
                : 'Checkout выключен: AI-провайдер не настроен.'}
            {' '}Выручка в Stars и расходы AI в USD — разные валюты, прибыль по ним не считается.
          </p>
          {data.daily.length > 1 && (
            <MiniBars
              title="Выручка Stars по дням"
              points={data.daily.map((day) => ({ key: day.date, label: formatDay(day.date), value: day.stars }))}
              format={formatStars}
            />
          )}
          {summary.isError && <p className="admin-footnote">Не удалось обновить. Показаны последние данные.</p>}
        </>
      ) : null}
    </section>
  )
}

function EntitlementsSection() {
  const entitlements = useQuery({
    queryKey: ['miniapp-admin-entitlements'],
    queryFn: ({ signal }) => getAdminEntitlements({ signal }),
    staleTime: 30_000,
  })
  const data = entitlements.data
  const soon = data?.items.filter((item) => item.expiring_soon) ?? []
  const rest = data?.items.filter((item) => !item.expiring_soon) ?? []
  const renderItem = (item: NonNullable<typeof data>['items'][number]) => (
    <li className={item.expiring_soon ? 'is-warn' : ''} key={item.chat_id}>
      <div className="admin-rows__main">
        <strong className="admin-rows__name">{item.chat_title ?? 'Без названия'}</strong>
        <span>{item.days_left} дн.</span>
      </div>
      <small>
        ID {item.chat_id} · до {formatDateTime(item.valid_until)}
        {item.last_purchase_at ? ` · покупка ${formatDateTime(item.last_purchase_at)}` : ''}
      </small>
    </li>
  )
  return (
    <section className="admin-section" aria-labelledby="ai-entitlements-title">
      <div className="admin-section__title-row">
        <h2 id="ai-entitlements-title">Активные подписки</h2>
        <SectionRetry onRetry={() => void entitlements.refetch()} busy={entitlements.isFetching} />
      </div>
      {entitlements.isPending ? <SectionSkeleton rows={3} /> : entitlements.isError && !data ? (
        <SectionError message={entitlements.error.message} onRetry={() => void entitlements.refetch()} busy={entitlements.isFetching} />
      ) : data ? (
        data.items.length === 0 ? <p className="admin-empty">Активных подписок Selara AI нет.</p> : (
          <>
            {soon.length > 0 && (
              <>
                <h3 className="admin-subheading">Истекают в ближайшие 7 дней</h3>
                <ul className="admin-rows">{soon.map(renderItem)}</ul>
              </>
            )}
            {rest.length > 0 && (
              <>
                {soon.length > 0 && <h3 className="admin-subheading">Остальные</h3>}
                <ul className="admin-rows">{rest.map(renderItem)}</ul>
              </>
            )}
            {data.active_paid_chats > data.items.length && (
              <p className="admin-footnote">Показаны ближайшие {data.items.length} из {formatCount(data.active_paid_chats)}.</p>
            )}
          </>
        )
      ) : null}
    </section>
  )
}

function PaymentCard({ payment }: { payment: AdminPayment }) {
  const [open, setOpen] = useState(false)
  const detail = useQuery({
    queryKey: ['miniapp-admin-payment-detail', payment.id],
    queryFn: ({ signal }) => getAdminPaymentDetail(payment.id, { signal }),
    enabled: open,
    staleTime: 60_000,
  })
  const rejected = payment.state === 'rejected'
  const refundPending = payment.refund?.status === 'pending'
  return (
    <li className={rejected ? 'admin-payment is-rejected' : 'admin-payment'}>
      <button
        className="admin-payment__head"
        type="button"
        aria-expanded={open}
        aria-label={`Платёж ${payment.id}, ${rejected ? 'отклонён' : 'применён'}, ${payment.amount_stars} Stars`}
        onClick={() => setOpen((value) => !value)}
      >
        <span className="admin-payment__top">
          <strong>#{payment.id} · {formatStars(payment.amount_stars)}</strong>
          <span className={rejected ? 'admin-badge is-warn' : 'admin-badge'}>{rejected ? '! Отклонён' : '✓ Применён'}</span>
        </span>
        <small>{formatDateTime(payment.payment_at)} · покупатель {payment.buyer_user_id}</small>
        <small className="admin-payment__chat">
          {payment.target_scope === 'user'
            ? `Selara Personal · пользователь ${payment.target_user_id ?? '—'}`
            : `${payment.chat_title ?? 'Чат недоступен'} · ${payment.chat_id ?? payment.target_chat_id ?? '—'}`}
        </small>
        {rejected && payment.reason && <small>Причина: {payment.reason}</small>}
        {payment.refund && <small>{refundLabels[payment.refund.status]}</small>}
      </button>
      {refundPending && <p className="admin-warning" role="note">Исход возврата требует ручной проверки.</p>}
      {open && (
        <div className="admin-payment__detail">
          {detail.isPending ? <SectionSkeleton rows={2} /> : detail.isError ? (
            <SectionError message={detail.error.message} onRetry={() => void detail.refetch()} busy={detail.isFetching} />
          ) : (
            <dl>
              <dt>Telegram charge ID</dt><dd className="admin-mono">{detail.data.telegram_payment_charge_id}</dd>
              <dt>Исходный / целевой чат</dt><dd>{detail.data.source_chat_id ?? '—'} / {detail.data.target_chat_id ?? '—'}</dd>
              <dt>Текущий чат</dt><dd>{detail.data.chat_id ?? '—'}</dd>
              <dt>Доступ сейчас</dt>
              <dd>
                {detail.data.entitlement
                  ? `${detail.data.entitlement.active_now ? 'активен' : 'не активен'} (${detail.data.entitlement.status}) до ${formatDateTime(detail.data.entitlement.valid_until)}`
                  : 'записи нет'}
              </dd>
              <dt>Счёт</dt>
              <dd>{detail.data.intent ? `${detail.data.intent.status}, условия ${detail.data.intent.terms_version ?? '—'}` : 'не найден'}</dd>
              {detail.data.refund && (
                <>
                  <dt>Возврат</dt>
                  <dd>{refundLabels[detail.data.refund.status]}{detail.data.refund.result_code ? ` · ${detail.data.refund.result_code}` : ''}</dd>
                </>
              )}
            </dl>
          )}
          {detail.data?.refund_command && (
            <p className="admin-footnote">Вернуть Stars владельцем: <code>{detail.data.refund_command}</code> в личке с ботом.</p>
          )}
        </div>
      )}
    </li>
  )
}

function PaymentsSection() {
  const [filters, setFilters] = useState<AdminPaymentFilters>(defaultFilters)
  const [chatInput, setChatInput] = useState('')
  const [buyerInput, setBuyerInput] = useState('')

  useEffect(() => {
    const timer = window.setTimeout(() => {
      const chatId = /^-?\d+$/.test(chatInput.trim()) ? chatInput.trim() : ''
      const buyerId = /^\d+$/.test(buyerInput.trim()) ? buyerInput.trim() : ''
      setFilters((current) => (current.chatId === chatId && current.buyerId === buyerId ? current : { ...current, chatId, buyerId }))
    }, 300)
    return () => window.clearTimeout(timer)
  }, [chatInput, buyerInput])

  const payments = useInfiniteQuery({
    queryKey: ['miniapp-admin-payments', filters],
    initialPageParam: null as string | null,
    queryFn: ({ signal, pageParam }) => getAdminPayments(filters, pageParam, { signal }),
    getNextPageParam: (lastPage) => lastPage.next_cursor ?? undefined,
    placeholderData: keepPreviousData,
    staleTime: 30_000,
  })
  const items = payments.data?.pages.flatMap((page) => page.items) ?? []

  return (
    <section className="admin-section" aria-labelledby="ai-payments-title">
      <div className="admin-section__title-row">
        <h2 id="ai-payments-title">История платежей</h2>
        <SectionRetry onRetry={() => void payments.refetch()} busy={payments.isFetching} />
      </div>
      <div className="admin-filter-grid">
        <label>
          <span>Статус</span>
          <select value={filters.state} onChange={(event) => setFilters({ ...filters, state: event.target.value as AdminPaymentFilters['state'] })}>
            <option value="all">Все</option>
            <option value="applied">Применён</option>
            <option value="rejected">Отклонён</option>
          </select>
        </label>
        <label>
          <span>Возврат</span>
          <select value={filters.refund} onChange={(event) => setFilters({ ...filters, refund: event.target.value as AdminPaymentFilters['refund'] })}>
            <option value="all">Любой</option>
            <option value="none">Без возврата</option>
            <option value="pending">В процессе</option>
            <option value="refunded">Выполнен</option>
            <option value="failed">Не удался</option>
          </select>
        </label>
        <label>
          <span>Период</span>
          <select
            value={filters.periodDays ?? ''}
            onChange={(event) => setFilters({ ...filters, periodDays: event.target.value ? Number(event.target.value) : null })}
          >
            <option value="">Всё время</option>
            {periods.map((period) => <option key={period.days} value={period.days}>{period.label}</option>)}
          </select>
        </label>
        <label>
          <span>ID чата</span>
          <input inputMode="numeric" value={chatInput} onChange={(event) => setChatInput(event.target.value)} placeholder="-100…" />
        </label>
        <label>
          <span>ID покупателя</span>
          <input inputMode="numeric" value={buyerInput} onChange={(event) => setBuyerInput(event.target.value)} placeholder="123…" />
        </label>
      </div>
      {payments.isPending ? <SectionSkeleton rows={4} /> : payments.isError && !payments.data ? (
        <SectionError message={payments.error.message} onRetry={() => void payments.refetch()} busy={payments.isFetching} />
      ) : (
        <>
          {items.length === 0 ? <p className="admin-empty">Платежей Selara AI пока нет.</p> : (
            <ul className="admin-payments" aria-busy={payments.isFetching}>
              {items.map((payment) => <PaymentCard key={payment.id} payment={payment} />)}
            </ul>
          )}
          {payments.hasNextPage && (
            <button className="admin-load-more" type="button" disabled={payments.isFetchingNextPage} onClick={() => void payments.fetchNextPage()}>
              {payments.isFetchingNextPage ? 'Загружаю…' : 'Показать ещё'}
            </button>
          )}
          {payments.isError && <p className="admin-footnote">Не удалось обновить. Показаны последние данные.</p>}
        </>
      )}
    </section>
  )
}

export function AdminAiPage() {
  const [periodDays, setPeriodDays] = useState(30)
  usePageTitle('AI и монетизация · Selara Admin')

  return (
    <section className="admin-page admin-ai">
      <header className="admin-page__heading">
        <p className="admin-eyebrow">Selara AI</p>
        <h1>AI и монетизация</h1>
        <Link className="admin-back-link" to={routes.admin}>‹ Обзор системы</Link>
      </header>
      <ReadinessSection />
      <AdminModelsSection />
      <AdminFeatureRoutesSection />
      <AdminQuotaModeSection />
      <PeriodSwitch value={periodDays} onChange={setPeriodDays} />
      <AiSummarySection periodDays={periodDays} />
      <BreakdownSection periodDays={periodDays} />
      <StarsSection periodDays={periodDays} />
      <EntitlementsSection />
      <PaymentsSection />
    </section>
  )
}
