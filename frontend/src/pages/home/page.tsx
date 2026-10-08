import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'

import { routes } from '@/shared/config/routes'
import { usePageTitle } from '@/shared/lib/use-page-title'
import { getMiniAppPage } from '@/shared/miniapp/api'
import { groupLetter, groupRoleText, mergeGroups } from '@/shared/miniapp/group-utils'
import type { MiniAppHomePageData } from '@/shared/miniapp/model'
import { useMiniApp } from '@/shared/miniapp/use-miniapp'
import { LoadingShell } from '@/shared/ui/LoadingShell'

function parseXp(value: string) {
  const match = value.replace(/\s/g, '').match(/(\d+)\/(\d+)/)
  if (!match) return null
  const current = parseInt(match[1], 10)
  const total = parseInt(match[2], 10)
  return total > 0 ? { current, total } : null
}

export function HomePage() {
  const { viewer } = useMiniApp()
  const homeQuery = useQuery({
    queryKey: ['miniapp-home'],
    queryFn: () => getMiniAppPage<MiniAppHomePageData>('/miniapp/home', 'Не удалось загрузить главный экран.'),
  })

  usePageTitle('Главная')

  if (homeQuery.isLoading || (!homeQuery.data && !homeQuery.isError)) {
    return <LoadingShell eyebrow="Главная" title="Загружаю данные" cards={3} />
  }

  if (homeQuery.isError || !homeQuery.data) {
    return (
      <div className="v2">
        <h1 className="v2-title">Главная</h1>
        <div className="v2-error" role="alert">
          <span>{homeQuery.error?.message ?? 'Не удалось загрузить главный экран.'}</span>
          <button type="button" onClick={() => void homeQuery.refetch()}>Повторить</button>
        </div>
      </div>
    )
  }

  const data = homeQuery.data
  const adminGroups = data.admin_groups ?? []
  const chats = mergeGroups(data.recent_groups ?? [], adminGroups)
  const games = data.recent_games ?? []
  const isNew = chats.length === 0
  const isAdmin = adminGroups.length > 0

  const metrics = data.metrics ?? []
  const find = (re: RegExp) => metrics.find((m) => re.test(m.label))
  const level = find(/уровень|level/i)
  const xp = find(/опыт|xp/i)
  const balance = find(/баланс|очки|points|pts/i)
  const streak = find(/серия|streak|дней/i)
  const xpParsed = xp ? parseXp(xp.value) : null
  const xpPercent = xpParsed ? Math.min(100, Math.round((xpParsed.current / xpParsed.total) * 100)) : 0
  const firstName = viewer.display_name.split(' ')[0]

  if (isNew) {
    return (
      <div className="v2">
        <h1 className="v2-title" style={{ marginTop: 26 }}>Selara в вашем чате</h1>
        <p className="v2-sub" style={{ fontSize: 14, lineHeight: 1.55 }}>
          Статистика, игры и достижения работают прямо внутри Telegram-группы. Здесь вы увидите свои чаты и партии.
        </p>
        <div className="v2-steps">
          <div className="v2-step"><i>1</i><div><b>Добавьте бота в группу</b><span>Бот начнёт считать активность с этого момента.</span></div></div>
          <div className="v2-step"><i>2</i><div><b>Начните первую партию</b><span>Напишите /game в группе.</span></div></div>
          <div className="v2-step"><i>3</i><div><b>Посмотрите команды</b><span>Напишите /help — там весь список.</span></div></div>
        </div>
        <a className="v2-btn" href={data.bot_add_url} target="_blank" rel="noreferrer">Добавить бота в группу</a>
        <p className="v2-muted" style={{ textAlign: 'center', fontSize: 12 }}>Ваши чаты появятся здесь после первой активности.</p>
      </div>
    )
  }

  const manageChat = adminGroups[0]

  return (
    <div className="v2">
      <div style={{ marginTop: 22 }}>
        <div style={{ fontSize: 13, color: 'var(--text-3)' }}>Сегодня</div>
        <h1 className="v2-title" style={{ marginTop: 6 }}>{firstName}, с возвращением</h1>
      </div>

      <div className="v2-sec">
        <span>Ваши чаты</span>
        <Link to={routes.groups}>все</Link>
      </div>
      {chats.slice(0, 3).map((group) => (
        <Link key={group.chat_id} className="v2-row" to={routes.chat(group.chat_id)}>
          <span className="v2-ava v2-ava--sm">{groupLetter(group)}</span>
          <span className="v2-main">
            <b>{group.title}</b>
            <span>{groupRoleText(group)} · {(group.message_count ?? 0).toLocaleString('ru-RU')} сообщений</span>
          </span>
          <span className="v2-aside">{group.last_seen_at}</span>
        </Link>
      ))}

      {level || xp || balance || streak ? (
        <>
          <div className="v2-sec"><span>Ваш прогресс</span></div>
          <div style={{ marginTop: 14 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12.5, color: 'var(--text-2)' }}>
              <span>Уровень {level?.value ?? '1'}</span>
              <span>{xp?.value ?? ''}</span>
            </div>
            <div className="v2-progress"><i style={{ width: `${xpPercent}%` }} /></div>
            <div className="v2-stats">
              {balance ? <span><b>{balance.value}</b> pts</span> : null}
              {streak ? <span><b>{streak.value}</b> дней подряд</span> : null}
            </div>
          </div>
        </>
      ) : null}

      {isAdmin && manageChat ? (
        <>
          <div className="v2-sec"><span>Управление</span></div>
          <Link className="v2-row" style={{ marginTop: 6 }} to={routes.chatTab(manageChat.chat_id, 'settings')}>
            <span className="v2-main"><b style={{ fontWeight: 600, fontSize: 14 }}>Настройки группы</b></span>
            <span className="v2-aside">{manageChat.title} ›</span>
          </Link>
          <Link className="v2-row" to={routes.audit(manageChat.chat_id)}>
            <span className="v2-main"><b style={{ fontWeight: 600, fontSize: 14 }}>Аудит действий</b></span>
            <span className="v2-aside">{manageChat.title} ›</span>
          </Link>
          <Link className="v2-row" to={routes.chatTab(manageChat.chat_id, 'overview')}>
            <span className="v2-main"><b style={{ fontWeight: 600, fontSize: 14 }}>Лидерборд</b></span>
            <span className="v2-aside">{manageChat.title} ›</span>
          </Link>
        </>
      ) : null}

      <div className="v2-sec"><span>Последние партии</span></div>
      {games.length > 0 ? (
        games.map((game) => (
          <Link key={game.game_id} className="v2-row" style={{ padding: '13px 0' }} to={routes.games}>
            <span className="v2-main">
              <b style={{ fontWeight: 600, fontSize: 13.5 }}>{game.title}</b>
              <span>{game.chat_title} · {game.result_text}</span>
            </span>
            <span className="v2-aside">{game.started_at}</span>
          </Link>
        ))
      ) : (
        <p className="v2-muted">Здесь появятся завершённые партии из ваших чатов.</p>
      )}
    </div>
  )
}
