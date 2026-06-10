import { useQuery } from '@tanstack/react-query'

import { usePageTitle } from '@/shared/lib/use-page-title'
import { getMiniAppPage } from '@/shared/miniapp/api'
import type { MiniAppGroupsPageData } from '@/shared/miniapp/model'
import { MiniGroupSection } from '@/shared/miniapp/ui'
import { LoadingShell } from '@/shared/ui/LoadingShell'

export function GroupsPage() {
  const groupsQuery = useQuery({
    queryKey: ['miniapp-groups'],
    queryFn: () => getMiniAppPage<MiniAppGroupsPageData>('/miniapp/groups', 'Не удалось загрузить список групп.'),
  })

  usePageTitle('Чаты')

  if (groupsQuery.isLoading) {
    return <LoadingShell eyebrow="Группы" title="Собираю список групп" cards={3} />
  }

  if (groupsQuery.isError) {
    return <section className="miniapp-empty-card">{groupsQuery.error.message}</section>
  }

  if (!groupsQuery.data) {
    return <LoadingShell eyebrow="Группы" title="Подгружаю доступные чаты" cards={3} />
  }

  const adminGroups = groupsQuery.data.admin_groups || []
  const activityGroups = groupsQuery.data.activity_groups || []

  // Combine unique groups to count sums
  const uniqueGroups = new Map()
  adminGroups.forEach((g) => uniqueGroups.set(g.chat_id, g))
  activityGroups.forEach((g) => uniqueGroups.set(g.chat_id, g))

  const totalGroups = uniqueGroups.size
  let totalMessages = 0
  uniqueGroups.forEach((g) => {
    totalMessages += g.message_count || 0
  })

  return (
    <div className="miniapp-page-stack">
      <div>
        <div className="eyebrow">Группы</div>
        <h1 className="page">Ваши чаты</h1>
        <div className="page-sub">
          {totalGroups} групп · {totalMessages.toLocaleString()} сообщений суммарно
        </div>
      </div>

      <MiniGroupSection
        title="Управляемые"
        text="Чаты, где у аккаунта есть права на управление ботом."
        items={adminGroups}
        emptyText="Пока нет групп с управленческим доступом."
      />

      <MiniGroupSection
        title="Активность"
        text="Текущая активность и видимость групп для пользователя."
        items={activityGroups}
        emptyText="После активности в чатах здесь появятся группы."
      />

      <a
        className="btn ghost block"
        style={{ marginTop: '10px' }}
        href="https://t.me/Selara_Bot?startgroup=true"
        target="_blank"
        rel="noreferrer"
      >
        ＋ Добавить бота в группу
      </a>
    </div>
  )
}
