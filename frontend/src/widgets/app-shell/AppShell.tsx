import { useQuery } from '@tanstack/react-query'
import { NavLink, Outlet } from 'react-router-dom'

import { resolveAppPath } from '@/shared/config/app-base-path'
import { appNavigation } from '@/shared/config/routes'
import { getAppViewer } from '@/widgets/app-shell/api/get-app-viewer'
import { ProfileMenu } from '@/widgets/app-shell/ProfileMenu'

export function AppShell() {
  const viewerQuery = useQuery({
    queryKey: ['app-viewer'],
    queryFn: getAppViewer,
    retry: false,
  })

  return (
    <div className="app-shell">
      <aside className="app-sidebar">
        <div className="app-brand">
          <span className="app-brand__mark" />
          <div>
            <strong>Selara</strong>
            <p>Веб-панель</p>
          </div>
        </div>

        <nav className="app-nav" aria-label="Основная навигация">
          {appNavigation.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              className={({ isActive }) =>
                isActive ? 'app-nav__link app-nav__link--active' : 'app-nav__link'
              }
              end={item.to === '/app'}
            >
              {item.label}
            </NavLink>
          ))}
        </nav>

        <div className="app-sidebar__spacer" />

        <div className="app-sidebar__footer">
          {viewerQuery.data ? (
            <ProfileMenu viewer={viewerQuery.data} />
          ) : viewerQuery.isError ? (
            <form method="post" action={resolveAppPath('/logout')}>
              <button type="submit" className="button app-sidebar__logout">
                Выйти
              </button>
            </form>
          ) : (
            <div className="app-profile app-profile--loading">
              <span className="app-profile__avatar app-profile__avatar--placeholder" />
              <div className="app-profile__meta">
                <strong>Загружаю профиль…</strong>
                <span>сессия браузера</span>
              </div>
            </div>
          )}
        </div>
      </aside>

      <div className="app-content">
        <header className="app-topbar">
          <div>
            <span className="app-topbar__eyebrow">Панель Selara</span>
            <h1>Управление и статистика</h1>
          </div>
          <p className="app-topbar__note">
            Веб-клиент использует те же серверные маршруты, что и существующая панель, и работает с ней параллельно.
          </p>
        </header>

        <main className="app-main">
          <Outlet />
        </main>
      </div>
    </div>
  )
}
