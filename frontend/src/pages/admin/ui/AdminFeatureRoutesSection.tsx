import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { getFeatureRoutes, saveFeatureRoute } from '../api/admin-models'
import { SectionError, SectionRetry, SectionSkeleton } from './AdminAiParts'
import './admin-models.css'

export function AdminFeatureRoutesSection() {
  const client = useQueryClient()
  const state = useQuery({ queryKey: ['admin-feature-routes'], queryFn: getFeatureRoutes, staleTime: 15_000 })
  const [busyKey, setBusyKey] = useState<string | null>(null)
  const [error, setError] = useState('')
  const [saved, setSaved] = useState(false)
  const data = state.data

  async function change(routeKey: string, profileKey: string) {
    setError('')
    setSaved(false)
    setBusyKey(routeKey)
    try {
      await saveFeatureRoute(routeKey, profileKey || null)
      setSaved(true)
      await client.invalidateQueries({ queryKey: ['admin-feature-routes'] })
    } catch (failure) { setError(failure instanceof Error ? failure.message : 'Не удалось сохранить.') }
    finally { setBusyKey(null) }
  }

  return <section className="admin-section admin-models" aria-labelledby="admin-feature-routes-title">
    <div className="admin-section__title-row"><h2 id="admin-feature-routes-title">Модели для групп</h2><SectionRetry busy={state.isFetching} onRetry={() => void state.refetch()} /></div>
    {state.isPending ? <SectionSkeleton rows={3} /> : state.isError && !data ? (
      <SectionError message={state.error.message} busy={state.isFetching} onRetry={() => void state.refetch()} />
    ) : data ? <>
      <p className="admin-footnote">{data.fallback_note} Саммари и Personal AI настраиваются отдельно.</p>
      {data.items.map((route) => <article className="admin-model-card" key={route.route_key}>
        <strong>{route.title}</strong>
        <label>Профиль
          <select value={route.profile_key ?? ''} disabled={busyKey === route.route_key} onChange={(event) => void change(route.route_key, event.target.value)}>
            <option value="">По умолчанию (LLM_MODEL)</option>
            {data.profiles.map((profile) => <option key={profile.profile_key} value={profile.profile_key}>{profile.display_name}</option>)}
          </select>
        </label>
        <p className="admin-mono">Сейчас: {route.effective_model_id}{route.is_fallback && route.profile_key ? ' (профиль недоступен, fallback)' : ''}</p>
      </article>)}
      {saved && <p role="status">Сохранено. Применяется без рестарта в течение {data.applies_within_seconds} секунд.</p>}
      {error && <p role="alert" className="admin-warning">{error}</p>}
    </> : null}
  </section>
}
