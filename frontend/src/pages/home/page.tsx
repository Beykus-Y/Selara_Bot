import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'

import { routes } from '@/shared/config/routes'
import { usePageTitle } from '@/shared/lib/use-page-title'
import { getMiniAppPage } from '@/shared/miniapp/api'
import { useMiniApp } from '@/shared/miniapp/context'
import type { MiniAppHomePageData } from '@/shared/miniapp/model'
import { LoadingShell } from '@/shared/ui/LoadingShell'

export function HomePage() {
  const { viewer } = useMiniApp()
  const homeQuery = useQuery({
    queryKey: ['miniapp-home'],
    queryFn: () => getMiniAppPage<MiniAppHomePageData>('/miniapp/home', 'Не удалось загрузить главный экран.'),
  })

  usePageTitle('Главная')

  if (homeQuery.isLoading) {
    return <LoadingShell eyebrow="Главная" title="Собираю личный кабинет" cards={3} />
  }

  if (homeQuery.isError) {
    return <section className="miniapp-empty-card">{homeQuery.error.message}</section>
  }

  if (!homeQuery.data) {
    return <LoadingShell eyebrow="Главная" title="Загружаю данные" cards={3} />
  }

  // Extract metrics dynamically
  const metrics = homeQuery.data.metrics || []
  const levelMetric = metrics.find((m) => /уровень|level/i.test(m.label))
  const xpMetric = metrics.find((m) => /опыт|xp/i.test(m.label))
  const balanceMetric = metrics.find((m) => /баланс|очки|points|pts/i.test(m.label))
  const streakMetric = metrics.find((m) => /серия|streak|дней/i.test(m.label))

  const levelValue = levelMetric ? levelMetric.value : '1'
  const xpText = xpMetric ? xpMetric.value : '0 / 100 XP'
  const balanceValue = balanceMetric ? balanceMetric.value : '0'
  const streakValue = streakMetric ? streakMetric.value : '0'

  // Calculate XP percentage for progress bar and avatar ring
  let xpPercent = 0
  if (xpMetric) {
    const cleanXpValue = xpMetric.value.replace(/\s/g, '')
    const match = cleanXpValue.match(/(\d+)\/(\d+)/)
    if (match) {
      const current = parseInt(match[1], 10)
      const total = parseInt(match[2], 10)
      if (total > 0) {
        xpPercent = Math.min(100, Math.round((current / total) * 100))
      }
    }
  }

  const strokeOffset = 188.5 - (188.5 * xpPercent) / 100
  const initials = viewer.initials || viewer.display_name.slice(0, 2)

  return (
    <div className="miniapp-page-stack">
      {/* Profile Hero Card */}
      <div className="profile-hero">
        <div className="profile-top">
          <div className="avatar-ring">
            <svg viewBox="0 0 64 64">
              <circle className="track" cx="32" cy="32" r="30" />
              <circle
                className="prog"
                cx="32"
                cy="32"
                r="30"
                style={{ strokeDashoffset: strokeOffset }}
              />
            </svg>
            <div className="avatar">
              {viewer.avatar_url ? (
                <img src={viewer.avatar_url} alt={viewer.display_name} />
              ) : (
                initials
              )}
            </div>
            <div className="lvl-badge">{levelValue}</div>
          </div>
          <div>
            <div className="profile-name">{viewer.display_name}</div>
            <div className="profile-handle">
              {viewer.username ? `@${viewer.username}` : 'Telegram-аккаунт'}
            </div>
          </div>
        </div>

        <div className="xp-row">
          <div className="xp-meta">
            <span>Уровень {levelValue}</span>
            <span className="mono">{xpText}</span>
          </div>
          <div className="bar">
            <i style={{ width: `${xpPercent}%` }}></i>
          </div>
        </div>

        <div className="balance-row">
          <div className="stat">
            <div className="k">Баланс</div>
            <div className="v gold">
              {balanceValue} <small>pts</small>
            </div>
          </div>
          <div className="stat">
            <div className="k">Серия дней</div>
            <div className="v">
              {streakValue} <small>🔥</small>
            </div>
          </div>
        </div>
      </div>

      {/* Quick Actions */}
      <h2 className="sec">Быстрые действия</h2>
      <div className="quick">
        <Link className="q accent" to={routes.gacha}>
          <div className="ico">🎰</div>
          <b>Крутить баннер</b>
          <span>коллекция и крутки</span>
        </Link>
        <Link className="q" to={routes.more}>
          <div className="ico">🎁</div>
          <b>Ежедневный бонус</b>
          <span>доступен в боте</span>
        </Link>
        <Link className="q" to={routes.games}>
          <div className="ico">🎮</div>
          <b>Игровой центр</b>
          <span>активные партии</span>
        </Link>
        <Link className="q" to={routes.groups}>
          <div className="ico">🏆</div>
          <b>Лидерборд</b>
          <span>активность чатов</span>
        </Link>
      </div>

      {/* Activity Feed */}
      <h2 className="sec">
        Лента <Link to={routes.groups}>все чаты</Link>
      </h2>
      <div className="card feed" style={{ padding: '6px 14px' }}>
        {homeQuery.data.recent_games.map((game) => (
          <Link key={game.game_id} className="row" to={routes.games}>
            <div className="dot violet">🃏</div>
            <div className="txt">
              <b>{game.title}</b>
              <span>{game.chat_title} · {game.result_text}</span>
            </div>
            <div className="when">{game.started_at}</div>
          </Link>
        ))}
        {homeQuery.data.recent_games.length === 0 && (
          <div style={{ padding: '12px 0', color: 'var(--text-3)', fontSize: '13px', textAlign: 'center' }}>
            История появится после первых завершённых игр.
          </div>
        )}
      </div>
    </div>
  )
}
