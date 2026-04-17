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

  usePageTitle('Home')

  if (homeQuery.isLoading) {
    return <LoadingShell eyebrow="Home" title="Собираю мобильный кабинет" cards={3} />
  }

  if (homeQuery.isError) {
    return <section className="miniapp-empty-card">{homeQuery.error.message}</section>
  }

  if (!homeQuery.data) {
    return <LoadingShell eyebrow="Home" title="Готовлю быстрые входы" cards={3} />
  }

  return (
    <div className="miniapp-page-stack">
      <section className="miniapp-hero-card">
        <span className="miniapp-hero-card__eyebrow">Home</span>
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
            Live games
          </Link>
        </div>

        <div className="miniapp-quick-grid">
          <Link className="miniapp-quick-action" to={routes.groups}>
            <strong>Groups</strong>
            <span>Список групп, overview и leaderboard.</span>
          </Link>
          <Link className="miniapp-quick-action" to={routes.games}>
            <strong>Games</strong>
            <span>Play/watch сценарии и личные действия.</span>
          </Link>
          <Link className="miniapp-quick-action" to={routes.gacha}>
            <strong>Gacha</strong>
            <span>Коллекция viewer и recent pulls.</span>
          </Link>
          <Link className="miniapp-quick-action" to={routes.more}>
            <strong>More</strong>
            <span>Docs, desktop, профиль и logout.</span>
          </Link>
        </div>
      </section>

      <MiniMetricGrid items={homeQuery.data.metrics} />

      <MiniGroupSection
        title="Recent groups"
        text="Чаты, которые реально видны текущему аккаунту в miniapp."
        items={homeQuery.data.recent_groups}
        emptyText="Список появится после первой активности в группах."
      />

      <MiniRecentGamesSection
        title="Recent games"
        text="Короткая история последних завершённых партий."
        items={homeQuery.data.recent_games}
        emptyText="После первых завершённых игр тут появится история."
      />

      <MiniDashboardPanel panel={homeQuery.data.global_dashboard} />
    </div>
  )
}
