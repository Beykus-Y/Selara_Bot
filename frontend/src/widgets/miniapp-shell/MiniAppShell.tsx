import { NavLink, Outlet, Link, useLocation } from 'react-router-dom'

import { miniappNavigation, routes } from '@/shared/config/routes'
import { useMiniApp } from '@/shared/miniapp/context'

function resolveShellMeta(pathname: string) {
  if (pathname.startsWith('/chat/')) {
    return {
      eyebrow: 'Group',
      title: 'Overview & leaderboard',
      backTo: routes.groups,
      backLabel: 'Groups',
    }
  }

  if (pathname === routes.groups) {
    return {
      eyebrow: 'Groups',
      title: 'Ваши чаты и доступ',
      backTo: null,
      backLabel: null,
    }
  }

  if (pathname === routes.games) {
    return {
      eyebrow: 'Games',
      title: 'Play / watch',
      backTo: null,
      backLabel: null,
    }
  }

  if (pathname === routes.gacha) {
    return {
      eyebrow: 'Gacha',
      title: 'Коллекция и профиль',
      backTo: null,
      backLabel: null,
    }
  }

  if (pathname === routes.more) {
    return {
      eyebrow: 'More',
      title: 'Профиль и ссылки',
      backTo: null,
      backLabel: null,
    }
  }

  return {
    eyebrow: 'Home',
    title: 'Mini App',
    backTo: null,
    backLabel: null,
  }
}

export function MiniAppShell() {
  const location = useLocation()
  const { viewer } = useMiniApp()
  const meta = resolveShellMeta(location.pathname)

  return (
    <div className="miniapp-shell">
      <header className="miniapp-shell__header">
        <div className="miniapp-shell__header-main">
          {meta.backTo ? (
            <Link className="miniapp-shell__back" to={meta.backTo}>
              {meta.backLabel}
            </Link>
          ) : (
            <span className="miniapp-shell__eyebrow">{meta.eyebrow}</span>
          )}
          <strong>{meta.title}</strong>
        </div>

        <NavLink className="miniapp-shell__viewer" to={routes.more}>
          {viewer.avatar_url ? (
            <img src={viewer.avatar_url} alt={viewer.display_name} />
          ) : (
            <span className="miniapp-shell__viewer-fallback">{viewer.initials}</span>
          )}
        </NavLink>
      </header>

      <main className="miniapp-shell__main">
        <Outlet />
      </main>

      <nav className="miniapp-tabbar" aria-label="Mini App navigation">
        {miniappNavigation.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            end={item.to === routes.home}
            className={({ isActive }) =>
              isActive ? 'miniapp-tabbar__item miniapp-tabbar__item--active' : 'miniapp-tabbar__item'
            }
          >
            <span>{item.label}</span>
          </NavLink>
        ))}
      </nav>
    </div>
  )
}
