import type { CollectionCard } from '@/shared/api/gachaClient'

interface CollectionGridProps {
  cards: CollectionCard[]
  banner: string
}

function getRarityClass(rarity: string): string {
  if (rarity === 'legendary') return 'l'
  if (rarity === 'epic') return 'e'
  if (rarity === 'rare') return 'r'
  return ''
}

export function CollectionGrid({ cards, banner }: CollectionGridProps) {
  if (cards.length === 0) {
    return (
      <div className="card">
        <div style={{ color: 'var(--text-3)', fontSize: '13px', textAlign: 'center', padding: '12px 0' }}>
          Коллекция пока пуста.
        </div>
      </div>
    )
  }

  return (
    <div className="coll-grid">
      {cards.map((card) => {
        const rarityClass = getRarityClass(card.rarity)
        const upgradeLevel = card.copies_owned - 1
        const constellationText = upgradeLevel > 0 ? (banner === 'hsr' ? `E${upgradeLevel}` : `C${upgradeLevel}`) : ''

        return (
          <div key={card.code} className={`coll ${rarityClass}`}>
            {card.name}
            {constellationText && <span className="c">{constellationText}</span>}
          </div>
        )
      })}
    </div>
  )
}
