import { useQuery } from '@tanstack/react-query'

import { getHomePage } from '@/pages/home/api/get-home-page'
import { HomePageView } from '@/pages/home/ui/HomePageView'
import { usePageTitle } from '@/shared/lib/use-page-title'
import { LoadingShell } from '@/shared/ui/LoadingShell'

export function HomePage() {
  const homeQuery = useQuery({
    queryKey: ['home-page'],
    queryFn: getHomePage,
  })
  usePageTitle('Главная')

  if (homeQuery.isLoading) {
    return <LoadingShell eyebrow="Главная" title="Загружаю кабинет" />
  }

  if (homeQuery.isError) {
    return <section className="home-error">{homeQuery.error.message}</section>
  }

  if (!homeQuery.data) {
    return <LoadingShell eyebrow="Главная" title="Готовлю карточки кабинета" />
  }

  return <HomePageView data={homeQuery.data} />
}
