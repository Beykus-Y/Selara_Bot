import { useEffect } from 'react'
import { NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom'

import { miniappNavigation, routes } from '@/shared/config/routes'
import { PanelGlyph } from '@/shared/ui/PanelGlyph'

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

const tabIcons: Record<string, { icon: 'grid' | 'chat' | 'gamepad' | 'spark' | 'dots'; labelRu: string }> = {
  [routes.home]: { icon: 'grid', labelRu: 'Главная' },
  [routes.groups]: { icon: 'chat', labelRu: 'Чаты' },
  [routes.games]: { icon: 'gamepad', labelRu: 'Игры' },
  [routes.gacha]: { icon: 'spark', labelRu: 'Гача' },
  [routes.more]: { icon: 'dots', labelRu: 'Профиль' },
}

type TelegramBackButton = {
  show: () => void
  hide: () => void
  onClick: (handler: () => void) => void
  offClick: (handler: () => void) => void
}

type TelegramWindow = Window & {
  Telegram?: { WebApp?: { BackButton: TelegramBackButton } }
}

export function MiniAppShell() {
  const location = useLocation()
  const navigate = useNavigate()
  const meta = resolveShellMeta(location.pathname)
  const isAdminArea = location.pathname === routes.admin || location.pathname.startsWith(`${routes.admin}/`)

  useEffect(() => {
    const tg = (window as TelegramWindow).Telegram?.WebApp
    if (!tg) return

    if (meta.backTo) {
      const backTo = meta.backTo
      tg.BackButton.show()
      const handleBackClick = () => {
        navigate(backTo)
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
      {!isAdminArea && <nav className="tabbar" aria-label="Навигация Mini App">
        {miniappNavigation.map((item) => {
          const tabInfo = tabIcons[item.to] || { icon: 'dots' as const, labelRu: item.label }
          return (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.to === routes.home}
              className={({ isActive }) =>
                isActive ? 'tab on' : 'tab'
              }
            >
              <span className="t-ico"><PanelGlyph kind={tabInfo.icon} /></span>
              <span className="t-lbl">{tabInfo.labelRu}</span>
            </NavLink>
          )
        })}
      </nav>}
    </div>
  )
}
