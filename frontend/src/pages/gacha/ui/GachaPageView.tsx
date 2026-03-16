import type { CollectionResponse } from '@/shared/api/gachaClient'
import { CollectionGrid } from './CollectionGrid'
import './gacha-page-view.css'

interface GachaPageViewProps {
  data: CollectionResponse
  activeBanner: 'genshin' | 'hsr'
  onBannerChange: (banner: 'genshin' | 'hsr') => void
}

const RARITY_EMOJIS: Record<string, string> = {
  common: '⬜',
  rare: '🟦',
  epic: '🟪',
  legendary: '🟨',
}

const BANNER_LABELS: Record<'genshin' | 'hsr', string> = {
  genshin: 'Genshin Impact',
  hsr: 'Honkai: Star Rail',
}

const BANNER_NOTES: Record<'genshin' | 'hsr', string> = {
  genshin: 'Коллекция персонажей и копий по баннеру Genshin.',
  hsr: 'Коллекция персонажей и копий по баннеру HSR.',
}

const RARITY_NAMES: Record<string, string> = {
  common: 'Обычные',
  rare: 'Редкие',
  epic: 'Эпические',
  legendary: 'Легендарные',
}

export function GachaPageView({ data, activeBanner, onBannerChange }: GachaPageViewProps) {
  const rarityStats = data.cards.reduce(
    (acc, card) => {
      acc[card.rarity] = (acc[card.rarity] || 0) + 1
      return acc
    },
    {} as Record<string, number>,
  )

  return (
    <div className="gacha-page">
      <div className="gacha-header">
        <div>
          <h1 className="gacha-title">🎲 Моя коллекция гачи</h1>
          <div className="gacha-banner">Баннер: {BANNER_LABELS[activeBanner]}</div>
          <p className="gacha-banner-note">{BANNER_NOTES[activeBanner]}</p>
        </div>
        <div className="gacha-banner-switch" role="tablist" aria-label="Выбор баннера">
          {(['genshin', 'hsr'] as const).map((banner) => (
            <button
              key={banner}
              type="button"
              className={banner === activeBanner ? 'gacha-banner-button gacha-banner-button--active' : 'gacha-banner-button'}
              onClick={() => onBannerChange(banner)}
            >
              {BANNER_LABELS[banner]}
            </button>
          ))}
        </div>
      </div>

      <div className="gacha-stats">
        <div className="stat-card">
          <div className="stat-label">Уникальные карты</div>
          <div className="stat-value">{data.total_unique}</div>
        </div>
        <div className="stat-card stat-card--secondary">
          <div className="stat-label">Всего копий</div>
          <div className="stat-value">{data.total_copies}</div>
        </div>
      </div>

      <div className="gacha-rarities">
        <h2 className="gacha-section-title">Распределение по редкости</h2>
        <div className="rarity-list">
          {Object.entries(rarityStats).map(([rarity, count]) => (
            <div key={rarity} className={`rarity-item rarity-item--${rarity}`}>
              <span className="rarity-emoji">{RARITY_EMOJIS[rarity] || '❓'}</span>
              <span className="rarity-name">{RARITY_NAMES[rarity] || rarity}</span>
              <span className="rarity-count">{count}</span>
            </div>
          ))}
        </div>
      </div>

      <div className="gacha-collection">
        <h2 className="gacha-section-title">Все карточки</h2>
        <CollectionGrid cards={data.cards} banner={data.banner} />
      </div>
    </div>
  )
}
