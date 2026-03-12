import { RouterProvider, createBrowserRouter } from 'react-router-dom'

import { AchievementsPage } from '@/pages/achievements/page'
import { AuditPage } from '@/pages/audit/page'
import { ChatPage } from '@/pages/chat/page'
import { DocsPage } from '@/pages/docs/page'
import { EconomyPage } from '@/pages/economy/page'
import { FamilyPage } from '@/pages/family/page'
import { GamesPage } from '@/pages/games/page'
import { HomePage } from '@/pages/home/page'
import { LandingPage } from '@/pages/landing/page'
import { LoginPage } from '@/pages/login/page'
import { NotFoundPage } from '@/pages/not-found/page'
import { SettingsPage } from '@/pages/settings/page'
import { routes } from '@/shared/config/routes'
import { AppShell } from '@/widgets/app-shell/AppShell'

const router = createBrowserRouter([
  {
    path: routes.landing,
    element: <LandingPage />,
  },
  {
    path: routes.login,
    element: <LoginPage />,
  },
  {
    path: routes.userDocs,
    element: <DocsPage variant="user" />,
  },
  {
    path: routes.adminDocs,
    element: <DocsPage variant="admin" />,
  },
  {
    path: routes.app,
    element: <AppShell />,
    children: [
      { index: true, element: <HomePage /> },
      { path: 'settings', element: <SettingsPage /> },
      { path: 'games', element: <GamesPage /> },
      { path: 'games/live', element: <GamesPage /> },
      { path: 'achievements', element: <AchievementsPage /> },
      { path: 'docs/user', element: <DocsPage variant="user" /> },
      { path: 'docs/admin', element: <DocsPage variant="admin" /> },
      { path: 'chat/:chatId', element: <ChatPage /> },
      { path: 'chat/:chatId/economy', element: <EconomyPage /> },
      { path: 'chat/:chatId/audit', element: <AuditPage /> },
      { path: 'family/:chatId', element: <FamilyPage /> },
    ],
  },
  {
    path: '*',
    element: <NotFoundPage />,
  },
])

export function AppRouter() {
  return <RouterProvider router={router} />
}
