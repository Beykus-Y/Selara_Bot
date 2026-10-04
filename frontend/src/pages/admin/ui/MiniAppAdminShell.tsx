import { NavLink, Outlet } from 'react-router-dom'

import { routes } from '@/shared/config/routes'
import { usePageTitle } from '@/shared/lib/use-page-title'

import './miniapp-admin.css'

const sections = [
  { to: routes.admin, label: 'Главная', icon: '⌂', end: true },
  { to: routes.adminFeedback, label: 'Feedback', icon: '◷', end: false },
  { to: routes.adminMonitoring, label: 'Мониторинг', icon: '⌁', end: false },
  { to: routes.adminMore, label: 'Ещё', icon: '···', end: false },
]

export function MiniAppAdminShell() {
  usePageTitle('Selara Admin')

  return (
    <div className="miniapp-admin">
      <header className="miniapp-admin__topbar">
        <span className="miniapp-admin__mark" aria-hidden="true">S</span>
        <div>
          <strong>Selara Admin</strong>
          <span>Закрытая панель</span>
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
            <span aria-hidden="true">{section.icon}</span>
            <small>{section.label}</small>
          </NavLink>
        ))}
      </nav>
    </div>
  )
}
