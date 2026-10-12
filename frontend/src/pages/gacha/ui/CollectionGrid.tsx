import type { CollectionCard } from '@/shared/api/gachaClient'

interface CollectionGridProps {
  cards: CollectionCard[]
  banner: string
}

function getRarityClass(rarity: string): string {
  if (rarity === 'mythic') return 'm'
  if (rarity === 'legendary') return 'l'
  if (rarity === 'epic') return 'e'
  if (rarity === 'rare') return 'r'
  return ''
}

// Duplicate-stage prefixes match the bot: Genshin constellations, HSR eidolons, ZZZ mindscapes.
const UPGRADE_PREFIX_BY_BANNER: Record<string, string> = { hsr: 'E', zzz: 'М' }
const DEFAULT_UPGRADE_PREFIX = 'C'
// The gacha service caps duplicate stages at 6; later copies are plain duplicates.
const MAX_UPGRADE_STAGE = 6

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
        const upgradeLevel = Math.min(card.copies_owned - 1, MAX_UPGRADE_STAGE)
        const constellationText = upgradeLevel > 0 ? (UPGRADE_PREFIX_BY_BANNER[banner] ?? DEFAULT_UPGRADE_PREFIX) + upgradeLevel : ''

        return (
          <div
            key={card.code}
            className={`coll ${rarityClass}`}
            style={
              card.image_url
                ? {
                    backgroundImage: `linear-gradient(180deg, transparent 40%, rgba(7, 8, 16, 0.85) 90%), url(${card.image_url})`,
                    backgroundSize: 'cover',
                    backgroundPosition: 'center',
                  }
                : undefined
            }
          >
            <span style={{ position: 'relative', zIndex: 1, textShadow: '0 1px 3px rgba(0,0,0,0.9)', color: '#ffffff' }}>
              {card.name}
            </span>
            {constellationText && <span className="c" style={{ zIndex: 2 }}>{constellationText}</span>}
          </div>
        )
      })}
    </div>
  )
}
