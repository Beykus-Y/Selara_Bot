import { RouterProvider, createBrowserRouter } from 'react-router-dom'

import { RouteErrorBoundary } from '@/app/router/RouteErrorBoundary'
import { ChatPage } from '@/pages/chat/page'
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
        { path: 'games', element: <GamesPage /> },
        { path: 'gacha', element: <GachaCollectionPage /> },
        { path: 'more', element: <MorePage /> },
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
