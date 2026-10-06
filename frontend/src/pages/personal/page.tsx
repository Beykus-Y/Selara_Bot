import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'

import { getPersonalOverview } from '@/pages/personal/api/personal'
import { quotaLine, subscriptionView } from '@/pages/personal/lib/view'
import type { PersonalOverview, PersonalProfile } from '@/pages/personal/model/types'
import { MemorySection } from '@/pages/personal/ui/MemorySection'
import { ProfileForm } from '@/pages/personal/ui/ProfileForm'
import { routes } from '@/shared/config/routes'
import { usePageTitle } from '@/shared/lib/use-page-title'
import { LoadingShell } from '@/shared/ui/LoadingShell'

import './ui/personal-page.css'

const OVERVIEW_KEY = ['miniapp-personal'] as const

function SubscriptionCard({ subscription }: { subscription: PersonalOverview['subscription'] }) {
  const view = subscriptionView(subscription)
  const quota = quotaLine(subscription.quota)
  const paid = subscription.tier === 'paid' || subscription.tier === 'owner'
  return (
    <div className="card personal-card">
      <div className="personal-status">
        <span className={`personal-badge is-${view.tone}`}>{view.badge}</span>
      </div>
      {view.note && <p className="personal-hint">{view.note}</p>}
      {subscription.available && quota && <p className="personal-line">{quota}</p>}
      {!subscription.ai_available && (
        <p className="personal-hint">Сейчас AI-провайдер недоступен: ответы в личке временно не работают.</p>
      )}
      {subscription.available && !paid && subscription.offer_available && subscription.price_stars !== null && (
        <div className="personal-cta">
          <a className="button button--primary" href={subscription.bot_url} target="_blank" rel="noreferrer">
            Оформить Selara Personal
          </a>
          <small>
            {subscription.price_stars} ⭐ на {subscription.duration_days} дн. Оплата в личке с ботом: откройте{' '}
            {subscription.purchase_command}.
          </small>
        </div>
      )}
      {subscription.available && paid && subscription.tier === 'paid' && subscription.offer_available && (
        <p className="personal-hint">Продлить подписку можно командой {subscription.purchase_command} в личке с ботом.</p>
      )}
    </div>
  )
}

export function PersonalPage() {
  usePageTitle('Моя Selara')
  const queryClient = useQueryClient()
  const [notice, setNotice] = useState<string | null>(null)

  const query = useQuery({
    queryKey: OVERVIEW_KEY,
    queryFn: ({ signal }) => getPersonalOverview(signal),
    staleTime: 15_000,
  })

  if (query.isPending) {
    return <LoadingShell eyebrow="Моя Selara" title="Загружаю профиль" cards={3} />
  }

  if (query.isError || !query.data) {
    return (
      <div className="miniapp-page-stack">
        <div>
          <div className="eyebrow">Личный AI</div>
          <h1 className="page">Моя Selara</h1>
        </div>
        <div className="card personal-card">
          <p className="personal-error" role="alert">
            {query.error instanceof Error ? query.error.message : 'Не удалось загрузить «Мою Selara».'}
          </p>
          <button type="button" className="button button--secondary" onClick={() => void query.refetch()}>
            Повторить
          </button>
        </div>
      </div>
    )
  }

  const data = query.data
  const handleSaved = (profile: PersonalProfile, message: string) => {
    queryClient.setQueryData<PersonalOverview>(OVERVIEW_KEY, (current) => (current ? { ...current, profile } : current))
    setNotice(message)
    // The auto-memory switch availability and the memory counter depend on the saved flags.
    void queryClient.invalidateQueries({ queryKey: OVERVIEW_KEY })
  }

  return (
    <div className="miniapp-page-stack personal-page">
      <div>
        <div className="eyebrow">Личный AI</div>
        <h1 className="page">Моя Selara</h1>
        <div className="page-sub">Профиль собеседника, память и подписка. Диалог идёт в личке с ботом.</div>
      </div>

      <h2 className="sec">Подписка</h2>
      <SubscriptionCard subscription={data.subscription} />

      <h2 className="sec">Настройки</h2>
      <div className="card personal-card">
        {notice && (
          <p className="personal-notice" role="status">
            {notice}
          </p>
        )}
        <ProfileForm
          key={data.profile.revision}
          profile={data.profile}
          options={data.options}
          autoMemoryAvailable={data.memory.auto_extract_available}
          onSaved={handleSaved}
        />
      </div>

      <MemorySection
        memoryEnabled={data.profile.memory_enabled}
        onChanged={() => void queryClient.invalidateQueries({ queryKey: OVERVIEW_KEY })}
      />

      <p className="personal-hint">
        Условия и данные: <Link to={routes.more}>раздел «Ещё»</Link>. История диалога очищается командой /ai_reset.
      </p>
    </div>
  )
}
