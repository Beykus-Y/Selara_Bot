import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import type { FormEvent } from 'react'
import { ConfirmationRequired, getQuotaMode, saveQuotaMode } from '../api/admin-models'
import { SectionError, SectionRetry, SectionSkeleton } from './AdminAiParts'
import './admin-models.css'

const confirmText = 'После включения разные модели будут расходовать разное количество AI Limits. ' +
  'Текущие лимиты 5/150 запросов перестанут использоваться. Включить AI Limits?'

function parseBudget(value: string): number | null {
  const trimmed = value.trim()
  if (!trimmed) return null
  return /^\d+$/.test(trimmed) ? Number(trimmed) : NaN
}

export function AdminQuotaModeSection() {
  const client = useQueryClient()
  const state = useQuery({ queryKey: ['admin-quota-mode'], queryFn: getQuotaMode, staleTime: 15_000 })
  const data = state.data
  const [mode, setMode] = useState<'requests' | 'ail'>(data?.quota_mode ?? 'requests')
  const [free, setFree] = useState(data?.free_daily_ail == null ? '' : String(data.free_daily_ail))
  const [paid, setPaid] = useState(data?.paid_daily_ail == null ? '' : String(data.paid_daily_ail))
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [saved, setSaved] = useState(false)

  const [previousData, setPreviousData] = useState(data)
  if (data && data !== previousData) {
    setPreviousData(data)
    setMode(data.quota_mode)
    setFree(data.free_daily_ail === null ? '' : String(data.free_daily_ail))
    setPaid(data.paid_daily_ail === null ? '' : String(data.paid_daily_ail))
  }

  async function submit(event: FormEvent) {
    event.preventDefault()
    setError('')
    setSaved(false)
    const freeValue = parseBudget(free)
    const paidValue = parseBudget(paid)
    if (Number.isNaN(freeValue) || Number.isNaN(paidValue)) { setError('AIL budget — целое число больше 0.'); return }
    if (mode === 'ail' && (freeValue === null || paidValue === null)) { setError('Сначала задайте Free/Paid AIL budget.'); return }
    const payload = { quota_mode: mode, free_daily_ail: freeValue, paid_daily_ail: paidValue }
    setBusy(true)
    try {
      try { await saveQuotaMode(payload) }
      catch (failure) {
        if (!(failure instanceof ConfirmationRequired)) throw failure
        if (!window.confirm(confirmText)) return
        await saveQuotaMode({ ...payload, confirm: true })
      }
      setSaved(true)
      await client.invalidateQueries({ queryKey: ['admin-quota-mode'] })
    } catch (failure) { setError(failure instanceof Error ? failure.message : 'Не удалось сохранить.') }
    finally { setBusy(false) }
  }

  return <section className="admin-section admin-quota" aria-labelledby="admin-quota-mode-title">
    <div className="admin-section__title-row"><h2 id="admin-quota-mode-title">Система лимитов Personal</h2><SectionRetry busy={state.isFetching} onRetry={() => void state.refetch()} /></div>
    {state.isPending ? <SectionSkeleton rows={2} /> : state.isError && !data ? (
      <SectionError message={state.error.message} busy={state.isFetching} onRetry={() => void state.refetch()} />
    ) : data ? <form className="admin-model-form" onSubmit={(event) => void submit(event)}>
      <p>Сейчас: <strong>{data.quota_mode === 'ail' ? 'AI Limits' : `запросы (${data.requests.free_daily}/${data.requests.paid_daily} в сутки)`}</strong></p>
      <fieldset className="admin-quota-mode">
        <legend>Режим</legend>
        <label className="admin-model-check"><input type="radio" name="quota-mode" checked={mode === 'requests'} onChange={() => setMode('requests')} />Запросы</label>
        <label className="admin-model-check"><input type="radio" name="quota-mode" checked={mode === 'ail'} onChange={() => setMode('ail')} />AI Limits</label>
      </fieldset>
      <label>Free AIL / сутки<input inputMode="numeric" value={free} onChange={(event) => setFree(event.target.value)} placeholder="не задано" /></label>
      <label>Personal AIL / сутки<input inputMode="numeric" value={paid} onChange={(event) => setPaid(event.target.value)} placeholder="не задано" /></label>
      <p className="admin-footnote">AIL — продуктовая единица расхода пользователя, не USD. Personal должен быть больше Free, максимум {data.max_daily_ail}. В режиме запросов AIL budget только сохраняется.</p>
      <ul className="admin-rows">
        {data.profiles.map((profile) => <li key={profile.profile_key}>
          <div className="admin-rows__main"><strong>{profile.display_name}</strong><span>×{profile.ail_multiplier} AIL</span></div>
          <small>{profile.available ? 'доступен пользователям' : 'недоступен (выключен, не назначен или некорректный multiplier)'}</small>
        </li>)}
      </ul>
      {data.activation_problems.length > 0 && <div className="admin-warning" role="note">
        <p>AI Limits нельзя включить:</p>
        <ul>{data.activation_problems.map((problem) => <li key={problem}>{problem}</li>)}</ul>
      </div>}
      {error && <p role="alert" className="admin-warning">{error}</p>}
      {saved && <p role="status">Сохранено. Применяется без рестарта в течение {data.applies_within_seconds} секунд.</p>}
      <div className="admin-model-actions"><button disabled={busy} type="submit">{busy ? 'Сохраняю…' : 'Сохранить'}</button></div>
    </form> : null}
  </section>
}
