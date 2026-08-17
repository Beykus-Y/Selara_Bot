import { useQuery } from '@tanstack/react-query'
import { useParams } from 'react-router-dom'

import { getMiniAppData } from '@/shared/miniapp/api'
import { usePageTitle } from '@/shared/lib/use-page-title'
import { LoadingShell } from '@/shared/ui/LoadingShell'

type AuditSummary = {
  total_rows: number
  rows: {
    event_id: number
    when: string
    action_label: string
    tone: string
    description: string
    actor_label: string
    target_label: string | null
  }[]
}

export function AuditPage() {
  const { chatId } = useParams()
  usePageTitle('Аудит')

  const query = useQuery({
    queryKey: ['miniapp-audit', chatId],
    queryFn: () =>
      getMiniAppData<{ audit: AuditSummary }>(
        `/miniapp/chat/${chatId}/audit`,
        'Не удалось загрузить журнал аудита.',
      ),
  })

  if (query.isLoading) {
    return <LoadingShell eyebrow="Аудит" title="Загружаю события" cards={3} />
  }

  if (query.isError) {
    return <section className="miniapp-empty-card">{query.error.message}</section>
  }

  const audit = query.data?.audit
  if (!audit) {
    return <LoadingShell eyebrow="Аудит" title="Готовлю журнал аудита" cards={3} />
  }

  return (
    <div className="miniapp-page-stack">
      <div>
        <span className="page-card__eyebrow">Аудит</span>
        <h1>Последние события</h1>
      </div>

      {audit.rows.length === 0 ? (
        <p className="miniapp-empty-card">Журнал пока пуст.</p>
      ) : (
        audit.rows.map((row) => (
          <article key={row.event_id} className="miniapp-empty-card">
            <p>
              <strong>{row.action_label}</strong> · {row.when}
            </p>
            <p>{row.description}</p>
            <p>
              {row.actor_label}
              {row.target_label ? ` → ${row.target_label}` : ''}
            </p>
          </article>
        ))
      )}
      <p className="miniapp-empty-card">
        Полный журнал с поиском и фильтрами пока доступен только в браузере.
      </p>
    </div>
  )
}
