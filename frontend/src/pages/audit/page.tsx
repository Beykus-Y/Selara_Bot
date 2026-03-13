import { useQuery } from '@tanstack/react-query'
import { useParams } from 'react-router-dom'

import { getAuditPage } from '@/pages/audit/api/get-audit-page'
import { AuditPageView } from '@/pages/audit/ui/AuditPageView'
import { usePageTitle } from '@/shared/lib/use-page-title'
import { useNamedEventSource } from '@/shared/lib/use-named-event-source'

const AUDIT_LIVE_EVENT_NAMES = ['chat_activity', 'new_vote', 'chat_refresh'] as const

export function AuditPage() {
  const { chatId } = useParams()

  const auditQuery = useQuery({
    queryKey: ['audit-page', chatId],
    queryFn: () => getAuditPage(chatId!),
    enabled: Boolean(chatId),
  })
  usePageTitle(auditQuery.data?.chat_title ? `${auditQuery.data.chat_title} / Аудит` : 'Журнал аудита')

  useNamedEventSource({
    enabled: Boolean(chatId),
    path: `/api/live/stream?scope=chat&chat_id=${chatId}`,
    eventNames: AUDIT_LIVE_EVENT_NAMES,
    onEvent: () => {
      void auditQuery.refetch()
    },
  })

  if (!chatId) {
    return <section className="audit-error">Не удалось определить ID чата.</section>
  }

  if (auditQuery.isLoading) {
    return <section className="audit-loading">Загружаю аудит…</section>
  }

  if (auditQuery.isError) {
    return <section className="audit-error">{auditQuery.error.message}</section>
  }

  if (!auditQuery.data) {
    return <section className="audit-loading">Данные аудита пока недоступны.</section>
  }

  return <AuditPageView chatId={chatId} data={auditQuery.data} />
}
