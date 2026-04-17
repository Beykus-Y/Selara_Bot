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

  usePageTitle('Groups')

  if (groupsQuery.isLoading) {
    return <LoadingShell eyebrow="Groups" title="Собираю список групп" cards={3} />
  }

  if (groupsQuery.isError) {
    return <section className="miniapp-empty-card">{groupsQuery.error.message}</section>
  }

  if (!groupsQuery.data) {
    return <LoadingShell eyebrow="Groups" title="Подгружаю доступные чаты" cards={3} />
  }

  return (
    <div className="miniapp-page-stack">
      <section className="miniapp-hero-card">
        <span className="miniapp-hero-card__eyebrow">Groups</span>
        <div className="miniapp-hero-card__headline">
          <div>
            <h1>{groupsQuery.data.hero_title}</h1>
            <p>{groupsQuery.data.hero_subtitle}</p>
          </div>
        </div>
      </section>

      <MiniGroupSection
        title="Managed groups"
        text="Чаты, где у аккаунта есть права на управление ботом."
        items={groupsQuery.data.admin_groups}
        emptyText="Пока нет групп с управленческим доступом."
      />

      <MiniGroupSection
        title="Activity groups"
        text="Текущая активность и видимость групп для viewer."
        items={groupsQuery.data.activity_groups}
        emptyText="После активности в чатах здесь появятся группы."
      />
    </div>
  )
}
