import type { CollectionCard } from '@/shared/api/gachaClient'
import './collection-grid.css'

interface CollectionGridProps {
  cards: CollectionCard[]
  banner: string
}

function resolveCollectionCardName(card: CollectionCard, banner: string): string {
  if (card.copies_owned <= 1) {
    return card.name
  }

  const upgradeLevel = Math.min(card.copies_owned - 1, 6)
  if (banner === 'genshin') {
    return `${card.name} (С${upgradeLevel})`
  }
  if (banner === 'hsr') {
    return `${card.name} (E${upgradeLevel})`
  }
  return `${card.name} ×${card.copies_owned}`
}

export function CollectionGrid({ cards, banner }: CollectionGridProps) {
  if (cards.length === 0) {
    return (
      <div className="collection-empty">
        <div className="collection-empty-state">
          <p className="collection-empty-title">Нет карточек в коллекции</p>
          <p className="collection-empty-note">Этот баннер пока пуст.</p>
        </div>
      </div>
    )
  }

  return (
    <div className="collection-grid">
      {cards.map((card) => {
        const displayName = resolveCollectionCardName(card, banner)
        return (
          <div key={card.code} className={`collection-card collection-card--${card.rarity}`}>
            <div className="collection-card-image-wrapper">
              <img src={card.image_url} alt={displayName} className="collection-card-image" />
            </div>
            <div className="collection-card-content">
              <h3 className="collection-card-name">{displayName}</h3>
              <div className="collection-card-rarity">{card.rarity_label}</div>
            </div>
          </div>
        )
      })}
    </div>
  )
}
