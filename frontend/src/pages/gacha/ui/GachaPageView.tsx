import type { CollectionResponse } from '@/shared/api/gachaClient'
import { CollectionGrid } from './CollectionGrid'
import './gacha-page-view.css'

interface GachaPageViewProps {
  data: CollectionResponse
}

const RARITY_EMOJIS: Record<string, string> = {
  common: '⬜',
  rare: '🟦',
  epic: '🟪',
  legendary: '🟨',
}

export function GachaPageView({ data }: GachaPageViewProps) {
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
        <h1 className="gacha-title">🎲 Моя коллекция гачи</h1>
        <div className="gacha-banner">Баннер: Genshin Impact</div>
      </div>

      <div className="gacha-stats">
        <div className="stat-card">
          <div className="stat-label">Уникальные карты</div>
          <div className="stat-value">{data.total_unique}</div>
        </div>
        <div className="stat-card">
          <div className="stat-label">Всего копий</div>
          <div className="stat-value">{data.total_copies}</div>
        </div>
      </div>

      <div className="gacha-rarities">
        <h2 className="gacha-section-title">Распределение по редкости</h2>
        <div className="rarity-list">
          {Object.entries(rarityStats).map(([rarity, count]) => (
            <div key={rarity} className="rarity-item">
              <span className="rarity-emoji">{RARITY_EMOJIS[rarity] || '❓'}</span>
              <span className="rarity-name">
                {rarity === 'common' && 'Обычные'}
                {rarity === 'rare' && 'Редкие'}
                {rarity === 'epic' && 'Эпические'}
                {rarity === 'legendary' && 'Легендарные'}
              </span>
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
