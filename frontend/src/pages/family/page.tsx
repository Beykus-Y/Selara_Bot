import { useQuery } from '@tanstack/react-query'
import { useParams } from 'react-router-dom'

import { getMiniAppData } from '@/shared/miniapp/api'
import { usePageTitle } from '@/shared/lib/use-page-title'
import { LoadingShell } from '@/shared/ui/LoadingShell'

type FamilySummary = {
  focus_label: string
  summary: { label: string; value: string }[]
  members: { id: number; label: string; role: string }[]
}

export function FamilyPage() {
  const { chatId } = useParams()
  usePageTitle('Моя семья')

  const query = useQuery({
    queryKey: ['miniapp-family', chatId],
    queryFn: () =>
      getMiniAppData<{ family: FamilySummary }>(`/miniapp/family/${chatId}`, 'Не удалось загрузить семью.'),
  })

  if (query.isLoading) {
    return <LoadingShell eyebrow="Моя семья" title="Загружаю связи" cards={2} />
  }

  if (query.isError) {
    return <section className="miniapp-empty-card">{query.error.message}</section>
  }

  const family = query.data?.family
  if (!family) {
    return <LoadingShell eyebrow="Моя семья" title="Готовлю экран семьи" cards={2} />
  }

  return (
    <div className="miniapp-page-stack">
      <div>
        <span className="page-card__eyebrow">Моя семья</span>
        <h1>{family.focus_label}</h1>
      </div>

      <article className="miniapp-empty-card">
        {family.summary.map((row) => (
          <p key={row.label}>
            {row.label}: {row.value}
          </p>
        ))}
        {family.members.length > 0 ? (
          <ul>
            {family.members.map((member) => (
              <li key={member.id}>
                {member.label} — {member.role}
              </li>
            ))}
          </ul>
        ) : (
          <p>Пока нет семейных связей в этой группе.</p>
        )}
        <p>
          Интерактивное семейное дерево пока доступно только в браузере —
          подробности через <code>/family</code> в чате.
        </p>
      </article>
    </div>
  )
}
