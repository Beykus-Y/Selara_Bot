import { NavLink, Outlet, Link, useLocation } from 'react-router-dom'

import { miniappNavigation, routes } from '@/shared/config/routes'

function resolveShellMeta(pathname: string) {
  if (pathname.startsWith('/chat/')) {
    return {
      title: 'Selara',
      subtitle: 'обзор и лидерборд',
      backTo: routes.groups,
      backLabel: 'Назад',
    }
  }

  if (pathname === routes.groups) {
    return {
      title: 'Selara',
      subtitle: 'ваши чаты и доступ',
      backTo: null,
      backLabel: null,
    }
  }

  if (pathname === routes.games) {
    return {
      title: 'Selara',
      subtitle: 'игровой центр',
      backTo: null,
      backLabel: null,
    }
  }

  if (pathname === routes.gacha) {
    return {
      title: 'Selara',
      subtitle: 'коллекция и крутки',
      backTo: null,
      backLabel: null,
    }
  }

  if (pathname === routes.more) {
    return {
      title: 'Selara',
      subtitle: 'профиль и ссылки',
      backTo: null,
      backLabel: null,
    }
  }

  return {
    title: 'Selara',
    subtitle: 'мини-приложение',
    backTo: null,
    backLabel: null,
  }
}

const tabIcons: Record<string, { icon: string; labelRu: string }> = {
  [routes.home]: { icon: '⌂', labelRu: 'Главная' },
  [routes.groups]: { icon: '👥', labelRu: 'Группы' },
  [routes.games]: { icon: '🎮', labelRu: 'Игры' },
  [routes.gacha]: { icon: '🎴', labelRu: 'Гача' },
  [routes.more]: { icon: '⋯', labelRu: 'Ещё' },
}

export function MiniAppShell() {
  const location = useLocation()
  const meta = resolveShellMeta(location.pathname)

  const handleClose = () => {
    const tg = (window as any).Telegram?.WebApp
    if (tg) {
      tg.close()
    }
  }

  return (
    <div className="miniapp-shell">
      {/* Telegram chrome */}
      <header className="tg-bar">
        {meta.backTo ? (
          <Link className="close" to={meta.backTo}>
            {meta.backLabel}
          </Link>
        ) : (
          <div className="close" onClick={handleClose}>
            Закрыть
          </div>
        )}
        <div className="title">
          <b>{meta.title}</b>
          <span>{meta.subtitle}</span>
        </div>
        <div className="dots">•••</div>
      </header>

      {/* Main Screen Scroll Area */}
      <main className="miniapp-shell__main">
        <Outlet />
      </main>

      {/* Bottom Navigation Tab Bar */}
      <nav className="tabbar" aria-label="Mini App navigation">
        {miniappNavigation.map((item) => {
          const tabInfo = tabIcons[item.to] || { icon: '⋯', labelRu: item.label }
          return (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.to === routes.home}
              className={({ isActive }) =>
                isActive ? 'tab on' : 'tab'
              }
            >
              <span className="t-ico">{tabInfo.icon}</span>
              <span className="t-lbl">{tabInfo.labelRu}</span>
            </NavLink>
          )
        })}
      </nav>
    </div>
  )
}
