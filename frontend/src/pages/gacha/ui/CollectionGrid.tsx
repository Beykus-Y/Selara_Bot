import type { CollectionCard } from '@/shared/api/gachaClient'
import './collection-grid.css'

interface CollectionGridProps {
  cards: CollectionCard[]
  banner: string
}

const RARITY_COLORS: Record<string, string> = {
  common: 'bg-gray-200',
  rare: 'bg-blue-200',
  epic: 'bg-purple-200',
  legendary: 'bg-yellow-200',
}

export function CollectionGrid({ cards }: CollectionGridProps) {
  if (cards.length === 0) {
    return (
      <div className="collection-empty">
        <div className="text-center py-8">
          <p className="text-gray-500">Нет карточек в коллекции</p>
        </div>
      </div>
    )
  }

  return (
    <div className="collection-grid">
      {cards.map((card) => (
        <div key={card.code} className={`collection-card ${RARITY_COLORS[card.rarity] || 'bg-white'}`}>
          <div className="collection-card-image-wrapper">
            <img src={card.image_url} alt={card.name} className="collection-card-image" />
          </div>
          <div className="collection-card-content">
            <h3 className="collection-card-name">{card.name}</h3>
            <div className="collection-card-rarity">{card.rarity_label}</div>
            <div className="collection-card-copies">×{card.copies_owned}</div>
          </div>
        </div>
      ))}
    </div>
  )
}
