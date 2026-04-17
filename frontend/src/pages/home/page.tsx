import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'

import { routes } from '@/shared/config/routes'
import { usePageTitle } from '@/shared/lib/use-page-title'
import { getMiniAppPage } from '@/shared/miniapp/api'
import { useMiniApp } from '@/shared/miniapp/context'
import type { MiniAppHomePageData } from '@/shared/miniapp/model'
import { MiniDashboardPanel, MiniGroupSection, MiniMetricGrid, MiniRecentGamesSection } from '@/shared/miniapp/ui'
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

  return (
    <div className="miniapp-page-stack">
      <section className="miniapp-hero-card">
        <span className="miniapp-hero-card__eyebrow">Личный кабинет</span>
        <div className="miniapp-hero-card__headline">
          <div>
            <h1>{homeQuery.data.hero_title}</h1>
            <p>{homeQuery.data.hero_subtitle}</p>
          </div>
          {viewer.avatar_url ? <img className="miniapp-profile-avatar" src={viewer.avatar_url} alt={viewer.display_name} /> : null}
        </div>

        <div className="miniapp-hero-card__actions">
          <a className="button button--secondary" href={homeQuery.data.desktop_url} target="_blank" rel="noreferrer">
            ПК-панель
          </a>
          <Link className="button button--primary" to={routes.games}>
            Игровой центр
          </Link>
        </div>

        <div className="miniapp-quick-grid">
          <Link className="miniapp-quick-action" to={routes.groups}>
            <strong>Группы</strong>
            <span>Список групп, статистика и лидерборд.</span>
          </Link>
          <Link className="miniapp-quick-action" to={routes.games}>
            <strong>Игры</strong>
            <span>Активные партии и личные действия.</span>
          </Link>
          <Link className="miniapp-quick-action" to={routes.gacha}>
            <strong>Коллекция</strong>
            <span>Ваши карточки и история круток.</span>
          </Link>
          <Link className="miniapp-quick-action" to={routes.more}>
            <strong>Ещё</strong>
            <span>Профиль, справка и выход.</span>
          </Link>
        </div>
      </section>

      <MiniMetricGrid items={homeQuery.data.metrics} />

      <MiniGroupSection
        title="Последние группы"
        text="Чаты, доступные вашему аккаунту в мини-приложении."
        items={homeQuery.data.recent_groups}
        emptyText="Список появится после первой активности в группах."
      />

      <MiniRecentGamesSection
        title="Последние партии"
        text="Краткая история завершённых игр."
        items={homeQuery.data.recent_games}
        emptyText="История появится после первых завершённых игр."
      />

      <MiniDashboardPanel panel={homeQuery.data.global_dashboard} />
    </div>
  )
}
