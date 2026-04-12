import { useQuery } from '@tanstack/react-query'
import { useParams, useSearchParams } from 'react-router-dom'

import { getAdminTablePage } from '@/pages/admin-table/api/admin-table-api'
import { AdminTablePageView } from '@/pages/admin-table/ui/AdminTablePageView'
import { usePageTitle } from '@/shared/lib/use-page-title'

export function AdminTablePage() {
  const { tableName } = useParams()
  const [searchParams, setSearchParams] = useSearchParams()

  const filters = Object.fromEntries(searchParams.entries())

  const query = useQuery({
    queryKey: ['admin-table', tableName, filters],
    queryFn: () => getAdminTablePage(tableName!, filters),
    enabled: Boolean(tableName),
  })
  usePageTitle(query.data?.table_title ?? 'Таблица')

  function handleFilter(newFilters: Record<string, string>) {
    setSearchParams(new URLSearchParams(newFilters))
  }

  if (!tableName) {
    return <div className="admin-table-loading">Не удалось определить имя таблицы.</div>
  }

  if (query.isLoading) {
    return <div className="admin-table-loading">Загружаю таблицу…</div>
  }

  if (query.isError) {
    return <div className="admin-table-loading">{query.error.message}</div>
  }

  if (!query.data) {
    return <div className="admin-table-loading">Данные недоступны.</div>
  }

  return (
    <AdminTablePageView
      tableName={tableName}
      data={query.data}
      filters={filters}
      onFilter={handleFilter}
    />
  )
}
