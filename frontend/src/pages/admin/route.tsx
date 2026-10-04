import { Navigate, Outlet } from 'react-router-dom'

import { routes } from '@/shared/config/routes'
import { useMiniApp } from '@/shared/miniapp/use-miniapp'

export function MiniAppAdminRoute() {
  const { permissions } = useMiniApp()
  return permissions.admin ? <Outlet /> : <Navigate to={routes.home} replace />
}
