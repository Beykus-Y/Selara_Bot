import { useQuery } from '@tanstack/react-query'
import { useParams } from 'react-router-dom'

import { getGachaCollection } from '@/pages/gacha/api/get-gacha-collection'
import { GachaPageView } from '@/pages/gacha/ui/GachaPageView'
import { usePageTitle } from '@/shared/lib/use-page-title'
import { LoadingShell } from '@/shared/ui/LoadingShell'

export function GachaCollectionPage() {
  const { userId } = useParams<{ userId: string }>()
  const parsedUserId = userId ? parseInt(userId, 10) : null

  usePageTitle('Коллекция гачи')

  const collectionQuery = useQuery({
    queryKey: ['gacha-collection', parsedUserId],
    queryFn: () => getGachaCollection(parsedUserId!, 'genshin'),
    enabled: !!parsedUserId,
  })

  if (!parsedUserId) {
    return <div className="error-message">Ошибка: не указан ID пользователя</div>
  }

  if (collectionQuery.isLoading) {
    return <LoadingShell eyebrow="Коллекция" title="Загружаю вашу коллекцию карточек" />
  }

  if (collectionQuery.isError) {
    return (
      <div className="error-container">
        <p className="error-message">Ошибка при загрузке коллекции</p>
        <p className="error-details">{collectionQuery.error instanceof Error ? collectionQuery.error.message : 'Неизвестная ошибка'}</p>
      </div>
    )
  }

  if (!collectionQuery.data) {
    return <LoadingShell eyebrow="Коллекция" title="Инициализирую коллекцию" />
  }

  return <GachaPageView data={collectionQuery.data} />
}
