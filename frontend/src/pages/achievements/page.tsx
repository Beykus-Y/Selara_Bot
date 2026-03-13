import { useQuery } from '@tanstack/react-query'

import { getAchievementsPage } from '@/pages/achievements/api/get-achievements-page'
import { AchievementsPageView } from '@/pages/achievements/ui/AchievementsPageView'
import { usePageTitle } from '@/shared/lib/use-page-title'
import { LoadingShell } from '@/shared/ui/LoadingShell'

export function AchievementsPage() {
  const achievementsQuery = useQuery({
    queryKey: ['achievements-page'],
    queryFn: getAchievementsPage,
  })
  usePageTitle('Достижения')

  if (achievementsQuery.isLoading) {
    return <LoadingShell eyebrow="Достижения" title="Собираю каталог наград" />
  }

  if (achievementsQuery.isError) {
    return <section className="achievements-error">{achievementsQuery.error.message}</section>
  }

  if (!achievementsQuery.data) {
    return <LoadingShell eyebrow="Достижения" title="Подгружаю прогресс аккаунта" />
  }

  return <AchievementsPageView data={achievementsQuery.data} />
}
