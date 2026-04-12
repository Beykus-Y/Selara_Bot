import { useQuery } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { useEffect } from 'react'

import { getAdminPage } from '@/pages/admin/api/get-admin-page'
import { AdminPageView } from '@/pages/admin/ui/AdminPageView'
import { usePageTitle } from '@/shared/lib/use-page-title'
import { routes } from '@/shared/config/routes'

export function AdminPage() {
  const navigate = useNavigate()

  const query = useQuery({
    queryKey: ['admin-page'],
    queryFn: getAdminPage,
    retry: false,
  })
  usePageTitle('Админ-панель')

  useEffect(() => {
    if (query.isError) {
      const msg = query.error.message
      if (msg.includes('401') || msg.includes('Требуется') || msg.includes('истекла')) {
        void navigate(routes.adminLogin)
      }
    }
  }, [query.isError, query.error, navigate])

  if (query.isLoading) {
    return <div className="admin-loading">Загружаю панель…</div>
  }

  if (query.isError) {
    return <div className="admin-error">{query.error.message}</div>
  }

  if (!query.data) {
    return <div className="admin-loading">Данные недоступны.</div>
  }

  return <AdminPageView data={query.data} />
}
