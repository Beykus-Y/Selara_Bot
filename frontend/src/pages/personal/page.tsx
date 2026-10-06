import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'

import { formatDate, quotaView } from '@/pages/chat/lib/ai-access-view'
import { getPersonalOverview } from '@/pages/personal/api/personal-api'
import { MemorySection } from '@/pages/personal/ui/MemorySection'
import { PrivacySection } from '@/pages/personal/ui/PrivacySection'
import type { PersonalOverview } from '@/pages/personal/model/types'
import { routes } from '@/shared/config/routes'
import { usePageTitle } from '@/shared/lib/use-page-title'
import { LoadingShell } from '@/shared/ui/LoadingShell'

import '@/pages/chat/ui/selara-ai-panel.css'
import './personal.css'

function subscriptionBadge(subscription: PersonalOverview['subscription']) {
  if (subscription.state === 'unavailable' || subscription.tier === null) {
    return { text: 'Не удалось проверить', tone: 'muted', note: 'Статус временно не удалось проверить. Это не значит, что доступ отключён.' }
  }
  if (subscription.tier === 'owner_internal') {
    return { text: 'Внутренний доступ', tone: 'ok', note: null }
  }
  if (subscription.tier === 'paid') {
    const until = subscription.valid_until ? `Активна до ${formatDate(subscription.valid_until, 'UTC')}` : 'Активна'
    return { text: subscription.expiring_soon ? 'Скоро закончится' : 'Активна', tone: subscription.expiring_soon ? 'warn' : 'ok', note: until }
  }
  return { text: 'Бесплатный доступ', tone: 'muted', note: null }
}

function SubscriptionSection({ data }: { data: PersonalOverview }) {
  const { subscription, quota } = data
  const badge = subscriptionBadge(subscription)
  const view = quotaView(quota, 'сегодня', 'UTC')
  const showOffer = subscription.offer_available && subscription.tier !== 'owner_internal' && subscription.state === 'available'
  return (
    <section className="miniapp-section-card selara-ai" aria-labelledby="personal-sub-title">
      <div className="miniapp-section-head">
        <h2 id="personal-sub-title">Selara Personal</h2>
      </div>
      <div className="selara-ai__status">
        <span className={`selara-ai__badge is-${badge.tone}`}>{badge.text}</span>
        {badge.note && <span className="selara-ai__note">{badge.note}</span>}
      </div>
      <dl className="selara-ai__list">
        <div className={`selara-ai__row is-${view.tone}`}>
          <dt>AI-запросы в личных сообщениях</dt>
          <dd>
            <strong>{view.headline}</strong>
            {view.percentUsed !== null && (
              <span
                className="selara-ai__bar"
                role="progressbar"
                aria-label={`Использовано ${view.percentUsed}%`}
                aria-valuemin={0}
                aria-valuemax={100}
                aria-valuenow={view.percentUsed}
              >
                <i style={{ width: `${view.percentUsed}%` }} />
              </span>
            )}
            {view.detail && <small>{view.detail}</small>}
          </dd>
        </div>
      </dl>
      {showOffer && (
        <div className="selara-ai__cta">
          <a className="button button--primary" href={subscription.purchase.bot_dm_url} target="_blank" rel="noreferrer">
            {subscription.active ? 'Продлить в Telegram' : 'Оформить в Telegram'}
          </a>
          <small>
            {subscription.price_stars !== null ? `${subscription.price_stars} ⭐ на ${subscription.duration_days} дн. ` : ''}
            Оплата в личке с ботом: откройте {subscription.purchase.command}.
          </small>
        </div>
      )}
    </section>
  )
}

export function PersonalPage() {
  usePageTitle('Моя Selara')
  const query = useQuery({
    queryKey: ['miniapp-personal'],
    queryFn: ({ signal }) => getPersonalOverview(signal),
    staleTime: 10_000,
  })

  if (query.isPending) {
    return <LoadingShell eyebrow="Моя Selara" title="Загружаю профиль, подписку и память" cards={3} />
  }
  if (query.isError && !query.data) {
    return (
      <section className="miniapp-empty-card" role="alert">
        <p>{query.error.message}</p>
        <button className="button button--secondary" type="button" onClick={() => void query.refetch()}>
          Повторить
        </button>
      </section>
    )
  }
  const data = query.data
  if (!data) return null

  return (
    <div className="miniapp-page-stack personal-page">
      <div>
        <span className="page-card__eyebrow">Моя Selara</span>
        <h1>Личный AI</h1>
        <p className="selara-ai__note">
          Подписка, лимиты и память вашего личного ассистента. Настройки характера и режим ролевой игры меняются в
          личке с ботом командой /ai. Эти данные видите только вы.
        </p>
      </div>
      <SubscriptionSection data={data} />
      <MemorySection data={data} />
      <PrivacySection />
      <Link className="button button--secondary" to={routes.more}>
        Назад
      </Link>
    </div>
  )
}
