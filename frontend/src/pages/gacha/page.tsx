import { useQuery } from '@tanstack/react-query'
import { useSearchParams } from 'react-router-dom'
import { useState } from 'react'

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

  usePageTitle('Гача')

  const collectionQuery = useQuery({
    queryKey: ['miniapp-gacha-collection', viewer.telegram_user_id, banner],
    queryFn: () => getUserCollection(viewer.telegram_user_id, banner),
  })

  const profileQuery = useQuery({
    queryKey: ['miniapp-gacha-profile', viewer.telegram_user_id, banner],
    queryFn: () => getUserProfile(viewer.telegram_user_id, banner, 6),
  })

  // Visual Pity State to match mock design
  const pityKey = `selara:gacha-pity:${viewer.telegram_user_id}:${banner}`
  const [pityCount] = useState(() => {
    const saved = localStorage.getItem(pityKey)
    return saved ? parseInt(saved, 10) : 23 // Default visual pity
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

  const rollsToGuarantee = 90 - pityCount

  return (
    <div className="miniapp-page-stack">
      {/* Title */}
      <div>
        <div className="eyebrow">Гача</div>
        <h1 className="page">Коллекция</h1>
        <div className="page-sub">Карточки, крутки и питы по баннеру</div>
      </div>

      {/* Banner Switch */}
      <div className="banner-switch">
        <button
          className={banner === 'genshin' ? 'on' : ''}
          onClick={() => {
            const nextParams = new URLSearchParams(searchParams)
            nextParams.delete('banner')
            setSearchParams(nextParams, { replace: true })
          }}
        >
          Genshin
        </button>
        <button
          className={banner === 'hsr' ? 'on' : ''}
          onClick={() => {
            const nextParams = new URLSearchParams(searchParams)
            nextParams.set('banner', 'hsr')
            setSearchParams(nextParams, { replace: true })
          }}
        >
          HSR
        </button>
      </div>

      {/* Pity Card */}
      <div className="card pity-card">
        <div className="pity-head">
          <b>До гаранта</b>
          <span className="mono">{pityCount} / 90</span>
        </div>
        <div className="bar">
          <i className="goldfill" style={{ width: `${(pityCount / 90) * 100}%` }}></i>
        </div>
        <div className="pity-note">
          Легендарная гарантирована через <b style={{ color: 'var(--text-2)' }}>{rollsToGuarantee} круток</b>. Soft-pity с 74-й.
        </div>
      </div>

      {/* Roll Invitation Info */}
      {showInviteText && (
        <div
          style={{
            background: 'var(--violet-soft)',
            color: 'var(--violet)',
            padding: '12px',
            borderRadius: '12px',
            fontSize: '12.5px',
            textAlign: 'center',
            border: '1px solid var(--line-strong)',
          }}
        >
          Крутки запускаются в Telegram-боте! Напишите боту: <code>гача {banner === 'hsr' ? 'хср' : 'генш'}</code>
        </div>
      )}

      {/* CTA Button */}
      <button
        className="btn primary block"
        type="button"
        onClick={() => {
          setShowInviteText(true)
          setTimeout(() => setShowInviteText(false), 5000)
        }}
      >
        🎰 Крутить ×1 · 1 600 pts
      </button>

      {/* Recent Pulls */}
      <h2 className="sec">Последние крутки</h2>
      {profileQuery.data.recent_pulls.length > 0 ? (
        <div>
          {profileQuery.data.recent_pulls.map((pull) => {
            const isLegendary = pull.rarity === 'legendary'
            const isEpic = pull.rarity === 'epic'
            const firstTwoLetters = pull.card_name.trim().slice(0, 2).toLowerCase() || '??'

            return (
              <div
                key={`${pull.pulled_at}-${pull.card_name}`}
                className={`drop ${isLegendary ? 'legendary' : isEpic ? 'epic' : ''}`}
              >
                <div className="drop-ava">{firstTwoLetters}</div>
                <div className="drop-body">
                  <div className="drop-name">
                    <b>{pull.card_name}</b>
                    {(isLegendary || isEpic) && (
                      <span className={`chip ${isLegendary ? 'legendary' : 'epic'}`}>
                        {isLegendary ? '★ легендарная' : 'эпическая'}
                      </span>
                    )}
                  </div>
                  <div className="drop-reward">
                    <span className="pts">+{pull.points} pts</span> · +{pull.adventure_xp_gained} XP
                  </div>
                </div>
                <div className="drop-when">
                  {pull.pulled_at}
                </div>
              </div>
            )
          })}
        </div>
      ) : (
        <div className="card" style={{ textAlign: 'center', padding: '24px' }}>
          <strong style={{ display: 'block', marginBottom: '4px' }}>Пока нет круток</strong>
          <p style={{ margin: 0, fontSize: '13px', color: 'var(--text-3)' }}>
            История появится после первых круток на этом баннере.
          </p>
        </div>
      )}

      {/* Collection Grid */}
      <h2 className="sec">
        Коллекция <span style={{ color: 'var(--text-3)' }}>всего карт: {collectionQuery.data.total_copies}</span>
      </h2>
      <CollectionGrid cards={collectionQuery.data.cards} banner={collectionQuery.data.banner} />
    </div>
  )
}
