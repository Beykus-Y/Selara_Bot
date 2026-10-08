import { useQuery } from '@tanstack/react-query'
import { useSearchParams } from 'react-router-dom'
import { useState } from 'react'

import { CollectionGrid } from '@/pages/gacha/ui/CollectionGrid'
import { getUserCollection, getUserProfile } from '@/shared/api/gachaClient'
import { usePageTitle } from '@/shared/lib/use-page-title'
import { LoadingShell } from '@/shared/ui/LoadingShell'

export function GachaCollectionPage() {
  const [searchParams, setSearchParams] = useSearchParams()
  const rawBanner = searchParams.get('banner')
  const banner = rawBanner === 'hsr' ? 'hsr' : 'genshin'

  usePageTitle('Гача')

  // The server resolves the viewer from the Mini App session: no telegram_user_id is sent.
  const collectionQuery = useQuery({
    queryKey: ['miniapp-gacha-collection', banner],
    queryFn: () => getUserCollection(banner),
  })

  const profileQuery = useQuery({
    queryKey: ['miniapp-gacha-profile', banner],
    queryFn: () => getUserProfile(banner, 6),
  })

  const [showInviteText, setShowInviteText] = useState(false)

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

  const rarityColor: Record<string, string> = {
    mythic: 'oklch(0.66 0.2 25)',
    legendary: 'oklch(0.84 0.13 80)',
    epic: 'oklch(0.7 0.16 320)',
    rare: 'oklch(0.76 0.12 250)',
  }
  const rarityLabel: Record<string, string> = {
    mythic: 'Мифическая',
    legendary: 'Легендарная',
    epic: 'Эпическая',
    rare: 'Редкая',
    common: 'Обычная',
  }
  const switchBanner = (next: 'genshin' | 'hsr') => {
    const nextParams = new URLSearchParams(searchParams)
    if (next === 'hsr') nextParams.set('banner', 'hsr')
    else nextParams.delete('banner')
    setSearchParams(nextParams, { replace: true })
  }

  return (
    <div className="v2">
      <h1 className="v2-title">Коллекция</h1>
      <p className="v2-sub">Карточки и история круток по баннеру.</p>

      <div className="v2-tabs" role="tablist" aria-label="Выбор баннера">
        <button type="button" role="tab" aria-selected={banner === 'genshin'} className={banner === 'genshin' ? 'on' : ''} onClick={() => switchBanner('genshin')}>Genshin</button>
        <button type="button" role="tab" aria-selected={banner === 'hsr'} className={banner === 'hsr' ? 'on' : ''} onClick={() => switchBanner('hsr')}>HSR</button>
      </div>

      <div style={{ marginTop: 20, display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12 }}>
        <span style={{ fontSize: 13, color: 'var(--text-2)', lineHeight: 1.5 }}>Крутки запускаются в Telegram-боте.</span>
        <button
          type="button"
          className="v2-pill-btn"
          onClick={() => {
            setShowInviteText(true)
            setTimeout(() => setShowInviteText(false), 4500)
          }}
        >
          Как крутить
        </button>
      </div>
      {showInviteText && (
        <p style={{ fontSize: 13, color: '#c9c4da', margin: '12px 0 0', lineHeight: 1.5 }}>
          Напишите боту в личку: <b style={{ color: 'var(--text)' }}>гача {banner === 'hsr' ? 'хср' : 'генш'}</b>
        </p>
      )}

      <div className="v2-sec"><span>Последние крутки</span></div>
      {profileQuery.data.recent_pulls.length > 0 ? (
        profileQuery.data.recent_pulls.map((pull) => {
          const color = rarityColor[pull.rarity] ?? '#3a3550'
          return (
            <div key={`${pull.pulled_at}-${pull.card_name}`} className="v2-row" style={{ cursor: 'default' }}>
              <span style={{ width: 4, height: 34, borderRadius: 2, flex: 'none', background: color }} />
              <span className="v2-main">
                <b style={{ fontSize: 14 }}>{pull.card_name}</b>
                <span style={{ color: pull.rarity === 'common' ? undefined : color, marginTop: 4 }}>
                  {rarityLabel[pull.rarity] ?? pull.rarity}
                </span>
              </span>
              <span style={{ textAlign: 'right', flex: 'none' }}>
                <span style={{ display: 'block', fontSize: 12.5, color: '#c9c4da' }}>+{pull.points} pts · +{pull.adventure_xp_gained} XP</span>
                <span style={{ display: 'block', fontSize: 11, color: 'var(--text-3)', marginTop: 3 }}>{pull.pulled_at}</span>
              </span>
            </div>
          )
        })
      ) : (
        <p className="v2-muted">История появится после первых круток на этом баннере.</p>
      )}

      <div className="v2-sec">
        <span>Коллекция</span>
        <span style={{ fontSize: 12, color: 'var(--text-3)' }}>всего карт: {collectionQuery.data.total_copies}</span>
      </div>
      <div style={{ marginTop: 14 }}>
        <CollectionGrid cards={collectionQuery.data.cards} banner={collectionQuery.data.banner} />
      </div>
    </div>
  )
}
