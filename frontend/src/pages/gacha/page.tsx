import { useQuery } from '@tanstack/react-query'
import { useSearchParams } from 'react-router-dom'

import { CollectionGrid } from '@/pages/gacha/ui/CollectionGrid'
import { getUserCollection, getUserProfile } from '@/shared/api/gachaClient'
import { usePageTitle } from '@/shared/lib/use-page-title'
import { useMiniApp } from '@/shared/miniapp/context'
import { LoadingShell } from '@/shared/ui/LoadingShell'

export function GachaCollectionPage() {
  const { viewer } = useMiniApp()
  const [searchParams, setSearchParams] = useSearchParams()
  const rawBanner = searchParams.get('banner')
  const banner = rawBanner === 'hsr' ? 'hsr' : 'genshin'

  usePageTitle('Gacha')

  const collectionQuery = useQuery({
    queryKey: ['miniapp-gacha-collection', viewer.telegram_user_id, banner],
    queryFn: () => getUserCollection(viewer.telegram_user_id, banner),
  })

  const profileQuery = useQuery({
    queryKey: ['miniapp-gacha-profile', viewer.telegram_user_id, banner],
    queryFn: () => getUserProfile(viewer.telegram_user_id, banner, 6),
  })

  if (collectionQuery.isLoading || profileQuery.isLoading) {
    return <LoadingShell eyebrow="Gacha" title="Загружаю коллекцию и профиль" cards={3} />
  }

  if (collectionQuery.isError) {
    return <section className="miniapp-empty-card">{collectionQuery.error.message}</section>
  }

  if (profileQuery.isError) {
    return <section className="miniapp-empty-card">{profileQuery.error.message}</section>
  }

  if (!collectionQuery.data || !profileQuery.data) {
    return <LoadingShell eyebrow="Gacha" title="Готовлю экран коллекции" cards={3} />
  }

  const profile = profileQuery.data.player

  return (
    <div className="miniapp-page-stack">
      <section className="miniapp-hero-card">
        <span className="miniapp-hero-card__eyebrow">Gacha</span>
        <div className="miniapp-hero-card__headline">
          <div>
            <h1>Коллекция и профиль</h1>
            <p>Только viewer-контур: без выбора произвольного `userId` и без interactive gacha actions в v1.</p>
          </div>
        </div>

        <div className="miniapp-hero-card__actions">
          {(['genshin', 'hsr'] as const).map((nextBanner) => (
            <button
              key={nextBanner}
              className={nextBanner === banner ? 'button button--primary' : 'button button--secondary'}
              type="button"
              onClick={() => {
                const nextParams = new URLSearchParams(searchParams)
                if (nextBanner === 'genshin') {
                  nextParams.delete('banner')
                } else {
                  nextParams.set('banner', nextBanner)
                }
                setSearchParams(nextParams, { replace: true })
              }}
            >
              {nextBanner === 'genshin' ? 'Genshin' : 'HSR'}
            </button>
          ))}
        </div>
      </section>

      <section className="miniapp-metric-grid">
        <article className="miniapp-metric-card miniapp-metric-card--violet">
          <span>Adventure rank</span>
          <strong>{profile.adventure_rank}</strong>
          <p>
            XP {profile.xp_into_rank} / {profile.xp_for_next_rank}
          </p>
        </article>
        <article className="miniapp-metric-card miniapp-metric-card--cyan">
          <span>Total points</span>
          <strong>{profile.total_points}</strong>
          <p>Primogems {profile.total_primogems}</p>
        </article>
        <article className="miniapp-metric-card miniapp-metric-card--magenta">
          <span>Unique cards</span>
          <strong>{profileQuery.data.unique_cards}</strong>
          <p>Total copies {profileQuery.data.total_copies}</p>
        </article>
        <article className="miniapp-metric-card miniapp-metric-card--indigo">
          <span>Collection grid</span>
          <strong>{collectionQuery.data.total_unique}</strong>
          <p>Всего копий {collectionQuery.data.total_copies}</p>
        </article>
      </section>

      <section className="miniapp-section-card">
        <div className="miniapp-section-head">
          <div>
            <h2>Recent pulls</h2>
            <p>Последние дропы текущего viewer по выбранному баннеру.</p>
          </div>
        </div>
        {profileQuery.data.recent_pulls.length > 0 ? (
          <div className="miniapp-list-stack">
            {profileQuery.data.recent_pulls.map((pull) => (
              <article key={`${pull.pulled_at}-${pull.card_name}`} className="miniapp-inline-card">
                <div>
                  <strong>{pull.card_name}</strong>
                  <p>{pull.rarity_label}</p>
                </div>
                <div className="miniapp-inline-card__meta">
                  <span>{pull.pulled_at}</span>
                  <span>+{pull.points} pts</span>
                  <span>+{pull.adventure_xp_gained} XP</span>
                </div>
              </article>
            ))}
          </div>
        ) : (
          <div className="miniapp-empty-card">
            <strong>Пока нет recent pulls</strong>
            <p>История появится после первых круток на этом баннере.</p>
          </div>
        )}
      </section>

      <section className="miniapp-section-card">
        <div className="miniapp-section-head">
          <div>
            <h2>Collection grid</h2>
            <p>Просмотр всей коллекции без pull/buy/currency действий.</p>
          </div>
        </div>
        <CollectionGrid cards={collectionQuery.data.cards} banner={collectionQuery.data.banner} />
      </section>
    </div>
  )
}
