import { useQuery } from '@tanstack/react-query'

import { getAchievementsPage } from '@/pages/achievements/api/get-achievements-page'
import { AchievementsPageView } from '@/pages/achievements/ui/AchievementsPageView'

export function AchievementsPage() {
  const achievementsQuery = useQuery({
    queryKey: ['achievements-page'],
    queryFn: getAchievementsPage,
  })

  if (achievementsQuery.isLoading) {
    return <section className="achievements-loading">Загружаю достижения…</section>
  }

  if (achievementsQuery.isError) {
    return <section className="achievements-error">{achievementsQuery.error.message}</section>
  }

  if (!achievementsQuery.data) {
    return <section className="achievements-loading">Данные достижений пока недоступны.</section>
  }

  return <AchievementsPageView data={achievementsQuery.data} />
}
