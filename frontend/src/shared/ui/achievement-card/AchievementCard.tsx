import './achievement-card.css'

type AchievementCardRow = {
  title: string
  meta: string
  value: string
  description: string
}

type AchievementCardProps = {
  row: AchievementCardRow
}

type ParsedAchievementRow = {
  icon: string
  title: string
  scope: string
  rarity: string
  status: string
  holdersPercent: number
  holdersCount: string
  awardedAt: string | null
}

const META_DELIMITER = ' • '
const HOLDERS_RE = /([\d.]+)%\s*\/\s*(\d+)/

function splitIconAndTitle(rawTitle: string) {
  const trimmed = rawTitle.trim()
  const firstSpaceIndex = trimmed.indexOf(' ')

  if (firstSpaceIndex <= 0) {
    return {
      icon: '★',
      title: trimmed || 'Достижение',
    }
  }

  return {
    icon: trimmed.slice(0, firstSpaceIndex),
    title: trimmed.slice(firstSpaceIndex + 1).trim() || trimmed,
  }
}

function clampPercent(value: number) {
  if (Number.isNaN(value)) {
    return 0
  }

  return Math.max(0, Math.min(value, 100))
}

function parseAchievementRow(row: AchievementCardRow): ParsedAchievementRow {
  const titleParts = splitIconAndTitle(row.title)
  const [scope = 'аккаунт', rarity = 'обычное', status = 'не получено', holdersRaw = '0.00% / 0'] =
    row.meta.split(META_DELIMITER).map((part) => part.trim())
  const holdersMatch = HOLDERS_RE.exec(holdersRaw)

  return {
    icon: titleParts.icon,
    title: titleParts.title,
    scope,
    rarity,
    status,
    holdersPercent: clampPercent(Number(holdersMatch?.[1] ?? '0')),
    holdersCount: holdersMatch?.[2] ?? '0',
    awardedAt: row.value !== 'не открыто' ? row.value : null,
  }
}

function rarityTone(rarity: string) {
  const normalized = rarity.toLowerCase()

  if (normalized.includes('леген')) {
    return 'legendary'
  }

  if (normalized.includes('эпич')) {
    return 'epic'
  }

  if (normalized.includes('редк')) {
    return 'rare'
  }

  if (normalized.includes('необыч')) {
    return 'uncommon'
  }

  return 'common'
}

export function AchievementCard({ row }: AchievementCardProps) {
  const parsed = parseAchievementRow(row)
  const tone = rarityTone(parsed.rarity)
  const isUnlocked = parsed.awardedAt !== null || parsed.status.toLowerCase() === 'получено'

  return (
    <article
      className={`achievement-card achievement-card--${tone}${isUnlocked ? ' achievement-card--unlocked' : ''}`}
    >
      <div className="achievement-card__icon" aria-hidden="true">
        <span>{parsed.icon}</span>
      </div>

      <div className="achievement-card__body">
        <div className="achievement-card__top">
          <div className="achievement-card__heading">
            <div className="achievement-card__badges">
              <span>{parsed.scope}</span>
              <span>{parsed.rarity}</span>
              <span>{parsed.status}</span>
            </div>
            <strong>{parsed.title}</strong>
          </div>

          <div className="achievement-card__date">
            <span>{isUnlocked ? 'Получено' : 'Статус'}</span>
            <strong>{parsed.awardedAt ?? 'Не открыто'}</strong>
          </div>
        </div>

        <p className="achievement-card__description">{row.description}</p>

        <div className="achievement-card__progress">
          <div className="achievement-card__progress-track" aria-hidden="true">
            <span style={{ width: `${parsed.holdersPercent}%` }} />
          </div>
          <div className="achievement-card__progress-meta">
            <span>Редкость каталога</span>
            <strong>
              {parsed.holdersPercent.toFixed(2)}% / {parsed.holdersCount}
            </strong>
          </div>
        </div>
      </div>
    </article>
  )
}
