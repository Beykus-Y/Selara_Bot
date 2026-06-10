import { useEffect } from 'react'
import { NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom'

import { miniappNavigation, routes } from '@/shared/config/routes'

function resolveShellMeta(pathname: string) {
  if (pathname.startsWith('/chat/')) {
    return {
      backTo: routes.groups,
    }
  }
  return {
    backTo: null,
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
  const navigate = useNavigate()
  const meta = resolveShellMeta(location.pathname)

  useEffect(() => {
    const tg = (window as any).Telegram?.WebApp
    if (!tg) return

    if (meta.backTo) {
      tg.BackButton.show()
      const handleBackClick = () => {
        navigate(meta.backTo!)
      }
      tg.BackButton.onClick(handleBackClick)
      return () => {
        tg.BackButton.offClick(handleBackClick)
        tg.BackButton.hide()
      }
    } else {
      tg.BackButton.hide()
    }
  }, [meta.backTo, navigate])

  return (
    <div className="miniapp-shell">
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
