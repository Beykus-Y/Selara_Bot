import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import type { FormEvent } from 'react'
import {
  createGrant,
  getGrantTarget,
  getPersonalEntitlements,
  getRecentGrants,
  lookupGrantTarget,
  revokeGrant,
} from '../api/admin-grants'
import type { GrantAction, GrantScope, LookupResult } from '../api/admin-grants'
import { formatDateTime } from '../lib/format'
import { SectionError, SectionRetry, SectionSkeleton } from './AdminAiParts'
import './admin-models.css'

type FormAction = 'grant' | 'shorten' | 'cancel_all'

const presets = [7, 30, 90, 365]
const actionLabels: Record<GrantAction, string> = {
  grant: 'выдана', extend: 'продлена', revoke: 'отключена', shorten: 'сокращена',
}

function newKey() {
  return typeof crypto !== 'undefined' && 'randomUUID' in crypto
    ? crypto.randomUUID()
    : `k${Date.now()}${Math.floor(Math.random() * 1e6)}`
}

function confirmText(action: FormAction, scope: GrantScope, id: number, days: number, paidRecently: boolean) {
  const subject = scope === 'user' ? `Selara Personal для пользователя ${id}` : `Selara AI для чата ${id}`
  if (action === 'grant') {
    return `Выдать ${subject} на ${days} дн.?${paidRecently ? ' У цели есть недавний платёж: дни добавятся к оплаченному сроку.' : ''}`
  }
  if (action === 'shorten') return `Убрать ${days} дн. у подписки: ${subject}?`
  return `Отключить полностью: ${subject}? Оплаченные Stars этой операцией не возвращаются, оплаченные дни тоже будут сняты.`
}

export function AdminGrantsSection() {
  const client = useQueryClient()
  const [open, setOpen] = useState(false)
  const [scope, setScope] = useState<GrantScope>('user')
  const [query, setQuery] = useState('')
  const [found, setFound] = useState<LookupResult | null>(null)
  const [targetId, setTargetId] = useState('')
  const [action, setAction] = useState<FormAction>('grant')
  const [days, setDays] = useState('30')
  const [reason, setReason] = useState('')
  const [notify, setNotify] = useState(true)
  const [key, setKey] = useState(newKey)
  const [busy, setBusy] = useState(false)
  const [searching, setSearching] = useState(false)
  const [error, setError] = useState('')
  const [done, setDone] = useState('')

  const parsedTarget = /^-?\d+$/.test(targetId.trim()) ? Number(targetId.trim()) : Number.NaN
  const targetValid = Number.isSafeInteger(parsedTarget) && (scope === 'user' ? parsedTarget > 0 : parsedTarget < 0)
  const target = useQuery({
    queryKey: ['admin-grant-target', scope, parsedTarget],
    queryFn: () => getGrantTarget(scope, parsedTarget),
    enabled: open && targetValid,
    staleTime: 5_000,
  })
  const recent = useQuery({ queryKey: ['admin-grants'], queryFn: getRecentGrants, enabled: open, staleTime: 15_000 })
  const personal = useQuery({
    queryKey: ['admin-personal-entitlements'], queryFn: getPersonalEntitlements, enabled: open, staleTime: 15_000,
  })

  async function search() {
    setError('')
    setSearching(true)
    try { setFound(await lookupGrantTarget(query)) }
    catch (failure) { setError(failure instanceof Error ? failure.message : 'Не удалось выполнить поиск.') }
    finally { setSearching(false) }
  }

  async function submit(event: FormEvent) {
    event.preventDefault()
    setError('')
    setDone('')
    if (!targetValid) {
      setError(scope === 'user' ? 'Id пользователя — положительное число.' : 'Id чата — отрицательное число.')
      return
    }
    const dayValue = Number(days)
    if (action !== 'cancel_all' && (!/^\d+$/.test(days.trim()) || dayValue < 1 || dayValue > 365)) {
      setError('Срок — целое число дней от 1 до 365.')
      return
    }
    const cleanReason = reason.trim()
    if (!cleanReason || cleanReason.length > 300) { setError('Причина обязательна, до 300 символов.'); return }
    if (!window.confirm(confirmText(action, scope, parsedTarget, dayValue, Boolean(target.data?.paid_recently)))) return
    setBusy(true)
    try {
      const common = { scope, target_id: parsedTarget, reason: cleanReason, idempotency_key: key, notify }
      const result = action === 'grant'
        ? await createGrant({ ...common, days: dayValue })
        : await revokeGrant({ ...common, mode: action, days: action === 'shorten' ? dayValue : null })
      const until = result.valid_until ? ` до ${formatDateTime(result.valid_until)}` : ''
      const tell = result.notified === true ? ' Получатель уведомлён.'
        : result.notified === false ? ' Уведомить получателя не удалось: операция выполнена.' : ''
      setDone(`Готово: подписка ${actionLabels[result.action]}${until}.${result.duplicate ? ' (повтор: ничего не изменилось)' : ''}${tell}`)
      setKey(newKey())
      await Promise.all([
        client.invalidateQueries({ queryKey: ['admin-grants'] }),
        client.invalidateQueries({ queryKey: ['admin-personal-entitlements'] }),
        client.invalidateQueries({ queryKey: ['admin-grant-target'] }),
        client.invalidateQueries({ queryKey: ['miniapp-admin-entitlements'] }),
      ])
    } catch (failure) { setError(failure instanceof Error ? failure.message : 'Не удалось выполнить операцию.') }
    finally { setBusy(false) }
  }

  return <section className="admin-section admin-grants" aria-labelledby="admin-grants-title">
    <div className="admin-section__title-row">
      <h2 id="admin-grants-title">Выдача подписок</h2>
      <button type="button" aria-expanded={open} onClick={() => setOpen((value) => !value)}>
        {open ? 'Свернуть' : 'Открыть'}
      </button>
    </div>
    {!open ? <p className="admin-footnote">Выдать или отозвать Selara Personal и подписку группы вручную. Оплата Stars не затрагивается.</p> : <>
      <form className="admin-model-form" onSubmit={(event) => void submit(event)}>
        <fieldset className="admin-quota-mode">
          <legend>Кому</legend>
          <label className="admin-model-check"><input type="radio" name="grant-scope" checked={scope === 'user'} onChange={() => { setScope('user'); setTargetId('') }} />Пользователю (Selara Personal)</label>
          <label className="admin-model-check"><input type="radio" name="grant-scope" checked={scope === 'chat'} onChange={() => { setScope('chat'); setTargetId('') }} />Группе (Selara AI)</label>
        </fieldset>
        <label>Поиск (id, @username или название чата)
          <input value={query} onChange={(event) => setQuery(event.target.value)} maxLength={120} />
        </label>
        <div className="admin-model-actions">
          <button type="button" disabled={searching || !query.trim()} onClick={() => void search()}>{searching ? 'Ищу…' : 'Найти'}</button>
        </div>
        {found && (found.users.length + found.chats.length === 0
          ? <p className="admin-empty">Ничего не найдено. Id можно ввести вручную.</p>
          : <ul className="admin-rows">
            {(scope === 'user' ? found.users : found.chats).map((item) => {
              const label = 'username' in item
                ? `${item.name ?? 'Без имени'}${item.username ? ` @${item.username}` : ''}${item.is_bot ? ' (бот)' : ''}`
                : (item.title ?? 'Без названия')
              return <li key={item.id}>
                <div className="admin-rows__main"><strong className="admin-rows__name">{label}</strong>
                  <button type="button" onClick={() => setTargetId(String(item.id))}>Выбрать</button></div>
                <small>ID {item.id}</small>
              </li>
            })}
          </ul>)}
        <label>{scope === 'user' ? 'Id пользователя' : 'Id чата (отрицательный)'}
          <input inputMode="numeric" value={targetId} onChange={(event) => setTargetId(event.target.value)} />
        </label>
        {targetValid && target.data && <p role="note">
          {target.data.title ?? `ID ${target.data.target_id}`}:{' '}
          {target.data.active ? `подписка до ${formatDateTime(target.data.valid_until)}${target.data.granted_by_admin ? ' (выдана администратором)' : ''}` : 'подписки нет'}
          {target.data.paid_recently ? '. Есть платёж Stars за последние 30 дней.' : ''}
        </p>}
        <fieldset className="admin-quota-mode">
          <legend>Действие</legend>
          <label className="admin-model-check"><input type="radio" name="grant-action" checked={action === 'grant'} onChange={() => setAction('grant')} />Выдать или продлить</label>
          <label className="admin-model-check"><input type="radio" name="grant-action" checked={action === 'shorten'} onChange={() => setAction('shorten')} />Убрать дни</label>
          <label className="admin-model-check"><input type="radio" name="grant-action" checked={action === 'cancel_all'} onChange={() => setAction('cancel_all')} />Отключить полностью</label>
        </fieldset>
        {action !== 'cancel_all' && <>
          <label>Дней (1–365)<input inputMode="numeric" value={days} onChange={(event) => setDays(event.target.value)} /></label>
          <div className="admin-model-actions">
            {presets.map((preset) => <button type="button" key={preset} onClick={() => setDays(String(preset))}>{preset} дн.</button>)}
          </div>
        </>}
        <label>Причина (попадёт в журнал)
          <input value={reason} onChange={(event) => setReason(event.target.value)} maxLength={300} />
        </label>
        <label className="admin-model-check"><input type="checkbox" checked={notify} onChange={(event) => setNotify(event.target.checked)} />Уведомить получателя</label>
        <p className="admin-footnote">
          Итоговый срок не дальше 730 дней от сегодня. «Отключить полностью» снимает и оплаченные дни; Stars не возвращаются.
        </p>
        {error && <p role="alert" className="admin-warning">{error}</p>}
        {done && <p role="status">{done}</p>}
        <div className="admin-model-actions"><button disabled={busy} type="submit">{busy ? 'Выполняю…' : 'Выполнить'}</button></div>
      </form>

      <h3 className="admin-subheading">Личные подписки</h3>
      {personal.isPending ? <SectionSkeleton rows={2} /> : personal.isError && !personal.data ? (
        <SectionError message={personal.error.message} busy={personal.isFetching} onRetry={() => void personal.refetch()} />
      ) : personal.data && personal.data.items.length === 0 ? <p className="admin-empty">Активных личных подписок нет.</p> : (
        <ul className="admin-rows">
          {personal.data?.items.map((item) => <li key={item.user_id}>
            <div className="admin-rows__main">
              <strong className="admin-rows__name">{item.name ?? 'Без имени'}{item.username ? ` @${item.username}` : ''}</strong>
              <span>{item.days_left} дн.</span>
            </div>
            <small>ID {item.user_id} · до {formatDateTime(item.valid_until)}{item.granted_by_admin ? ' · выдана администратором' : ''}</small>
          </li>)}
        </ul>
      )}

      <div className="admin-section__title-row">
        <h3 className="admin-subheading">Журнал выдач</h3>
        <SectionRetry busy={recent.isFetching} onRetry={() => void recent.refetch()} />
      </div>
      {recent.isPending ? <SectionSkeleton rows={2} /> : recent.isError && !recent.data ? (
        <SectionError message={recent.error.message} busy={recent.isFetching} onRetry={() => void recent.refetch()} />
      ) : recent.data && recent.data.items.length === 0 ? <p className="admin-empty">Выдач и отзывов пока не было.</p> : (
        <ul className="admin-rows">
          {recent.data?.items.map((row) => <li key={row.id}>
            <div className="admin-rows__main">
              <strong className="admin-rows__name">{row.scope === 'chat' ? (row.target_title ?? 'Чат') : 'Пользователь'} · {row.target_id}</strong>
              <span>{actionLabels[row.action]}{row.delta_days ? ` ${row.delta_days} дн.` : ''}</span>
            </div>
            <small>{formatDateTime(row.created_at)} · {row.reason}{row.notified === false ? ' · уведомление не доставлено' : ''}</small>
          </li>)}
        </ul>
      )}
    </>}
  </section>
}
