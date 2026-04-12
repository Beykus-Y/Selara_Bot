import { useQuery } from '@tanstack/react-query'
import { useParams, useSearchParams } from 'react-router-dom'

import { getAdminTableEditPage } from '@/pages/admin-table/api/admin-table-api'
import { AdminTableEditPageView } from '@/pages/admin-table/ui/AdminTableEditPageView'
import { usePageTitle } from '@/shared/lib/use-page-title'

export function AdminTableEditPage() {
  const { tableName } = useParams()
  const [searchParams] = useSearchParams()
  const pkQuery = searchParams.toString()

  const query = useQuery({
    queryKey: ['admin-table-edit', tableName, pkQuery],
    queryFn: () => getAdminTableEditPage(tableName!, pkQuery),
    enabled: Boolean(tableName),
  })
  usePageTitle(query.data ? `Редактирование · ${query.data.table_title}` : 'Редактирование')

  if (!tableName) {
    return <div className="admin-table-loading">Не удалось определить имя таблицы.</div>
  }

  if (query.isLoading) {
    return <div className="admin-table-loading">Загружаю запись…</div>
  }

  if (query.isError) {
    return <div className="admin-table-loading">{query.error.message}</div>
  }

  if (!query.data) {
    return <div className="admin-table-loading">Данные недоступны.</div>
  }

  return <AdminTableEditPageView data={query.data} />
}
