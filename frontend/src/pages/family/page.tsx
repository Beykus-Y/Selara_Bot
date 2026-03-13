import { useQuery } from '@tanstack/react-query'
import { useParams, useSearchParams } from 'react-router-dom'

import { getFamilyPage } from '@/pages/family/api/get-family-page'
import { FamilyPageView } from '@/pages/family/ui/FamilyPageView'
import { usePageTitle } from '@/shared/lib/use-page-title'

export function FamilyPage() {
  const { chatId } = useParams()
  const [searchParams] = useSearchParams()
  const userId = searchParams.get('user_id')

  const familyQuery = useQuery({
    queryKey: ['family-page', chatId, userId],
    queryFn: () => getFamilyPage(chatId!, userId),
    enabled: Boolean(chatId),
  })
  usePageTitle(familyQuery.data?.chat_title ? `${familyQuery.data.chat_title} / Моя семья` : 'Моя семья')

  if (!chatId) {
    return <section className="family-error">Не удалось определить ID чата.</section>
  }

  if (familyQuery.isLoading) {
    return <section className="family-loading">Загружаю семейный граф…</section>
  }

  if (familyQuery.isError) {
    return <section className="family-error">{familyQuery.error.message}</section>
  }

  if (!familyQuery.data) {
    return <section className="family-loading">Данные семейного графа пока недоступны.</section>
  }

  return <FamilyPageView chatId={chatId} data={familyQuery.data} />
}
