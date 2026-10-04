import { RouterProvider, createBrowserRouter } from 'react-router-dom'

import { RouteErrorBoundary } from '@/app/router/RouteErrorBoundary'
import { AuditPage } from '@/pages/audit/page'
import { MiniAppAdminRoute } from '@/pages/admin/route'
import { MiniAppAdminShell } from '@/pages/admin/ui/MiniAppAdminShell'
import { AdminDashboardPage } from '@/pages/admin/ui/AdminDashboardPage'
import { AdminFeedbackPage } from '@/pages/admin/ui/AdminFeedbackPage'
import { AdminMonitoringPage } from '@/pages/admin/ui/AdminMonitoringPage'
import { AdminMorePage } from '@/pages/admin/ui/AdminMorePage'
import { AdminBroadcastPage } from '@/pages/admin/ui/AdminBroadcastPage'
import { ChatPage } from '@/pages/chat/page'
import { EconomyPage } from '@/pages/economy/page'
import { FamilyPage } from '@/pages/family/page'
import { GachaCollectionPage } from '@/pages/gacha/page'
import { GamesPage } from '@/pages/games/page'
import { GroupsPage } from '@/pages/groups/page'
import { HomePage } from '@/pages/home/page'
import { MorePage } from '@/pages/more/page'
import { NotFoundPage } from '@/pages/not-found/page'
import { appBasePath } from '@/shared/config/app-base-path'
import { MiniAppShell } from '@/widgets/miniapp-shell/MiniAppShell'

const router = createBrowserRouter(
  [
    {
      path: '/',
      element: <MiniAppShell />,
      errorElement: <RouteErrorBoundary />,
      children: [
        { index: true, element: <HomePage /> },
        { path: 'groups', element: <GroupsPage /> },
        { path: 'chat/:chatId', element: <ChatPage /> },
        { path: 'chat/:chatId/economy', element: <EconomyPage /> },
        { path: 'chat/:chatId/audit', element: <AuditPage /> },
        { path: 'family/:chatId', element: <FamilyPage /> },
        { path: 'games', element: <GamesPage /> },
        { path: 'gacha', element: <GachaCollectionPage /> },
        { path: 'more', element: <MorePage /> },
        {
          path: 'admin',
          element: <MiniAppAdminRoute />,
          children: [
            {
              element: <MiniAppAdminShell />,
              children: [
                { index: true, element: <AdminDashboardPage /> },
                { path: 'feedback', element: <AdminFeedbackPage /> },
                { path: 'monitoring', element: <AdminMonitoringPage /> },
                { path: 'more', element: <AdminMorePage /> },
                { path: 'broadcast', element: <AdminBroadcastPage /> },
              ],
            },
          ],
        },
      ],
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
