import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { Link } from 'react-router-dom'

import { routes } from '@/shared/config/routes'
import { usePageTitle } from '@/shared/lib/use-page-title'
import { getMiniAppPage } from '@/shared/miniapp/api'
import { groupLetter, groupRoleText, isGroupLive, mergeGroups } from '@/shared/miniapp/group-utils'
import type { MiniAppGroupsPageData } from '@/shared/miniapp/model'
import { LoadingShell } from '@/shared/ui/LoadingShell'

type GroupsTab = 'manage' | 'join'

export function GroupsPage() {
  // null until data loads: the default tab depends on whether the viewer manages any chat.
  const [selectedTab, setTab] = useState<GroupsTab | null>(null)
  const groupsQuery = useQuery({
    queryKey: ['miniapp-groups'],
    queryFn: () => getMiniAppPage<MiniAppGroupsPageData>('/miniapp/groups', 'Не удалось загрузить список групп.'),
  })

  usePageTitle('Чаты')

  if (groupsQuery.isLoading || (!groupsQuery.data && !groupsQuery.isError)) {
    return <LoadingShell eyebrow="Чаты" title="Подгружаю доступные чаты" cards={3} />
  }

  if (groupsQuery.isError || !groupsQuery.data) {
    return (
      <div className="v2">
        <h1 className="v2-title">Чаты</h1>
        <div className="v2-error" role="alert">
          <span>{groupsQuery.error?.message ?? 'Не удалось загрузить чаты.'}</span>
          <button type="button" onClick={() => void groupsQuery.refetch()}>Повторить</button>
        </div>
      </div>
    )
  }

  const all = mergeGroups(groupsQuery.data.admin_groups ?? [], groupsQuery.data.activity_groups ?? [])
  const managed = all.filter((g) => g.is_admin)
  const joined = all.filter((g) => !g.is_admin)
  const tab: GroupsTab = selectedTab ?? (managed.length > 0 ? 'manage' : 'join')
  const shown = tab === 'manage' ? managed : joined
  const totalMessages = all.reduce((sum, g) => sum + (g.message_count || 0), 0)

  return (
    <div className="v2">
      <h1 className="v2-title">Чаты</h1>
      <p className="v2-sub">
        {all.length ? `${all.length} чатов · ${totalMessages.toLocaleString('ru-RU')} сообщений` : 'Пока нет чатов'}
      </p>
      <div className="v2-tabs" role="tablist">
        <button type="button" role="tab" aria-selected={tab === 'manage'} className={tab === 'manage' ? 'on' : ''} onClick={() => setTab('manage')}>
          Управляю · {managed.length}
        </button>
        <button type="button" role="tab" aria-selected={tab === 'join'} className={tab === 'join' ? 'on' : ''} onClick={() => setTab('join')}>
          Участвую · {joined.length}
        </button>
      </div>
      <p className="v2-hint">
        {tab === 'manage'
          ? 'Здесь чаты, где вы админ: лидерборд и аудит.'
          : 'Здесь видна ваша активность и лидерборд.'}
      </p>
      {shown.map((group) => (
        <Link key={group.chat_id} className="v2-row" style={{ padding: '15px 0' }} to={routes.chat(group.chat_id)}>
          <span className="v2-ava">{groupLetter(group)}</span>
          <span className="v2-main">
            <b>{group.title}</b>
            <span>{groupRoleText(group)} · {(group.message_count ?? 0).toLocaleString('ru-RU')} сообщений</span>
          </span>
          <span className={isGroupLive(group.last_seen_at) ? 'v2-aside v2-aside--live' : 'v2-aside'}>{group.last_seen_at}</span>
        </Link>
      ))}
      {shown.length === 0 ? (
        <p className="v2-muted">В этом списке пока пусто. Чаты появятся после активности бота.</p>
      ) : null}
      <a className="v2-link" href={groupsQuery.data.bot_add_url} target="_blank" rel="noreferrer">
        + Добавить бота в ещё один чат
      </a>
    </div>
  )
}
