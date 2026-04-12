import { RouterProvider, createBrowserRouter } from 'react-router-dom'

import { RouteErrorBoundary } from '@/app/router/RouteErrorBoundary'
import { AchievementsPage } from '@/pages/achievements/page'
import { AuditPage } from '@/pages/audit/page'
import { ChatPage } from '@/pages/chat/page'
import { DocsPage } from '@/pages/docs/page'
import { EconomyPage } from '@/pages/economy/page'
import { FamilyPage } from '@/pages/family/page'
import { FeedbackPage } from '@/pages/feedback/page'
import { GachaCollectionPage } from '@/pages/gacha/page'
import { GamesPage } from '@/pages/games/page'
import { HomePage } from '@/pages/home/page'
import { LandingPage } from '@/pages/landing/page'
import { LoginPage } from '@/pages/login/page'
import { NotFoundPage } from '@/pages/not-found/page'
import { SettingsPage } from '@/pages/settings/page'
import { AdminLoginPage } from '@/pages/admin-login/page'
import { AdminPage } from '@/pages/admin/page'
import { AdminBroadcastPage } from '@/pages/admin-broadcast/page'
import { AdminTablePage } from '@/pages/admin-table/page'
import { AdminTableEditPage } from '@/pages/admin-table/edit-page'
import { appBasePath } from '@/shared/config/app-base-path'
import { routes } from '@/shared/config/routes'
import { AppShell } from '@/widgets/app-shell/AppShell'

const router = createBrowserRouter(
  [
    {
      path: routes.landing,
      element: <LandingPage />,
      errorElement: <RouteErrorBoundary />,
    },
    {
      path: routes.login,
      element: <LoginPage />,
      errorElement: <RouteErrorBoundary />,
    },
    {
      path: routes.userDocs,
      element: <DocsPage variant="user" />,
      errorElement: <RouteErrorBoundary />,
    },
    {
      path: routes.adminDocs,
      element: <DocsPage variant="admin" />,
      errorElement: <RouteErrorBoundary />,
    },
    {
      path: routes.app,
      element: <AppShell />,
      errorElement: <RouteErrorBoundary />,
      children: [
        { index: true, element: <HomePage /> },
        { path: 'settings', element: <SettingsPage /> },
        { path: 'games', element: <GamesPage /> },
        { path: 'games/live', element: <GamesPage /> },
        { path: 'achievements', element: <AchievementsPage /> },
        { path: 'gacha/collection/:userId', element: <GachaCollectionPage /> },
        { path: 'docs/user', element: <DocsPage variant="user" /> },
        { path: 'docs/admin', element: <DocsPage variant="admin" /> },
        { path: 'chat/:chatId', element: <ChatPage /> },
        { path: 'chat/:chatId/economy', element: <EconomyPage /> },
        { path: 'chat/:chatId/audit', element: <AuditPage /> },
        { path: 'family/:chatId', element: <FamilyPage /> },
        { path: 'feedback', element: <FeedbackPage /> },
      ],
    },
    {
      path: '/admin/login',
      element: <AdminLoginPage />,
      errorElement: <RouteErrorBoundary />,
    },
    {
      path: '/admin',
      element: <AdminPage />,
      errorElement: <RouteErrorBoundary />,
    },
    {
      path: '/admin/broadcasts/:broadcastId',
      element: <AdminBroadcastPage />,
      errorElement: <RouteErrorBoundary />,
    },
    {
      path: '/admin/table/:tableName',
      element: <AdminTablePage />,
      errorElement: <RouteErrorBoundary />,
    },
    {
      path: '/admin/table/:tableName/edit',
      element: <AdminTableEditPage />,
      errorElement: <RouteErrorBoundary />,
    },
    {
      path: '*',
      element: <NotFoundPage />,
      errorElement: <RouteErrorBoundary />,
    },
  ],
  {
    basename: appBasePath || undefined,
  },
)

export function AppRouter() {
  return <RouterProvider router={router} />
}
