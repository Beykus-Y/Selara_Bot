import { NavLink, Outlet } from 'react-router-dom'

import { routes } from '@/shared/config/routes'
import { usePageTitle } from '@/shared/lib/use-page-title'
import { PanelGlyph } from '@/shared/ui/PanelGlyph'

import './miniapp-admin.css'

const sections = [
  { to: routes.admin, label: 'Главная', icon: 'grid', end: true },
  { to: routes.adminFeedback, label: 'Обращения', icon: 'docs', end: false },
  { to: routes.adminMonitoring, label: 'Мониторинг', icon: 'pulse', end: false },
  { to: routes.adminAi, label: 'AI', icon: 'spark', end: false },
  { to: routes.adminMore, label: 'Ещё', icon: 'dots', end: false },
] as const

export function MiniAppAdminShell() {
  usePageTitle('Selara Admin')

  return (
    <div className="miniapp-admin">
      <header className="miniapp-admin__topbar">
        <span className="miniapp-admin__mark" aria-hidden="true">S</span>
        <div>
          <strong>Selara Admin</strong>
          <span>Админ-панель</span>
        </div>
      </header>
      <main className="miniapp-admin__main">
        <Outlet />
      </main>
      <nav className="miniapp-admin__nav" aria-label="Навигация админки">
        {sections.map((section) => (
          <NavLink
            key={section.to}
            to={section.to}
            end={section.end}
            className={({ isActive }) => isActive ? 'miniapp-admin__nav-item is-active' : 'miniapp-admin__nav-item'}
          >
            <span aria-hidden="true" className="miniapp-admin__nav-icon"><PanelGlyph kind={section.icon} /></span>
            <small>{section.label}</small>
          </NavLink>
        ))}
      </nav>
    </div>
  )
}
