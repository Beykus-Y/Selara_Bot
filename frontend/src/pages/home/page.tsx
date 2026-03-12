import { useQuery } from '@tanstack/react-query'

import { getHomePage } from '@/pages/home/api/get-home-page'
import { HomePageView } from '@/pages/home/ui/HomePageView'

export function HomePage() {
  const homeQuery = useQuery({
    queryKey: ['home-page'],
    queryFn: getHomePage,
  })

  if (homeQuery.isLoading) {
    return <section className="home-loading">Загружаю главную страницу…</section>
  }

  if (homeQuery.isError) {
    return <section className="home-error">{homeQuery.error.message}</section>
  }

  if (!homeQuery.data) {
    return <section className="home-loading">Данные главной страницы пока недоступны.</section>
  }

  return <HomePageView data={homeQuery.data} />
}
