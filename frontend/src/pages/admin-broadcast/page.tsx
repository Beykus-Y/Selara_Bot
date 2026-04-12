import { useQuery } from '@tanstack/react-query'
import { useParams } from 'react-router-dom'

import { getBroadcastPage } from '@/pages/admin-broadcast/api/get-broadcast-page'
import { BroadcastPageView } from '@/pages/admin-broadcast/ui/BroadcastPageView'
import { usePageTitle } from '@/shared/lib/use-page-title'

export function AdminBroadcastPage() {
  const { broadcastId } = useParams()

  const query = useQuery({
    queryKey: ['admin-broadcast', broadcastId],
    queryFn: () => getBroadcastPage(broadcastId!),
    enabled: Boolean(broadcastId),
  })
  usePageTitle(query.data ? `Рассылка #${query.data.broadcast.id}` : 'Рассылка')

  if (!broadcastId) {
    return <div className="broadcast-loading">Не удалось определить ID рассылки.</div>
  }

  if (query.isLoading) {
    return <div className="broadcast-loading">Загружаю рассылку…</div>
  }

  if (query.isError) {
    return <div className="broadcast-loading">{query.error.message}</div>
  }

  if (!query.data) {
    return <div className="broadcast-loading">Данные недоступны.</div>
  }

  return <BroadcastPageView data={query.data} />
}
