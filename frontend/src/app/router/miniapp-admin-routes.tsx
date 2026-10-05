import type { RouteObject } from 'react-router-dom'

export const miniAppAdminRoute: RouteObject = {
  path: 'admin',
  lazy: async () => {
    const { MiniAppAdminRoute } = await import('@/pages/admin/route')
    return { Component: MiniAppAdminRoute }
  },
  children: [
    {
      lazy: async () => {
        const { MiniAppAdminShell } = await import('@/pages/admin/ui/MiniAppAdminShell')
        return { Component: MiniAppAdminShell }
      },
      children: [
        {
          index: true,
          lazy: async () => {
            const { AdminDashboardPage } = await import('@/pages/admin/ui/AdminDashboardPage')
            return { Component: AdminDashboardPage }
          },
        },
        {
          path: 'feedback',
          lazy: async () => {
            const { AdminFeedbackPage } = await import('@/pages/admin/ui/AdminFeedbackPage')
            return { Component: AdminFeedbackPage }
          },
        },
        {
          path: 'monitoring',
          lazy: async () => {
            const { AdminMonitoringPage } = await import('@/pages/admin/ui/AdminMonitoringPage')
            return { Component: AdminMonitoringPage }
          },
        },
        {
          path: 'ai',
          lazy: async () => {
            const { AdminAiPage } = await import('@/pages/admin/ui/AdminAiPage')
            return { Component: AdminAiPage }
          },
        },
        {
          path: 'more',
          lazy: async () => {
            const { AdminMorePage } = await import('@/pages/admin/ui/AdminMorePage')
            return { Component: AdminMorePage }
          },
        },
        {
          path: 'broadcast',
          lazy: async () => {
            const { AdminBroadcastPage } = await import('@/pages/admin/ui/AdminBroadcastPage')
            return { Component: AdminBroadcastPage }
          },
        },
      ],
    },
  ],
}
