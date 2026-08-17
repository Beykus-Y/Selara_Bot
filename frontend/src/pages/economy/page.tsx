import { useQuery } from '@tanstack/react-query'
import { useParams } from 'react-router-dom'

import { getMiniAppData } from '@/shared/miniapp/api'
import { usePageTitle } from '@/shared/lib/use-page-title'
import { LoadingShell } from '@/shared/ui/LoadingShell'

type EconomySummary = {
  balance: number
  farm_level: number
  farm_size_tier: string
  plots_ready: number
  plots_growing: number
  plots_empty: number
  inventory_item_count: number
}

export function EconomyPage() {
  const { chatId } = useParams()
  usePageTitle('Экономика')

  const query = useQuery({
    queryKey: ['miniapp-economy', chatId],
    queryFn: () =>
      getMiniAppData<{ economy: EconomySummary }>(
        `/miniapp/chat/${chatId}/economy`,
        'Не удалось загрузить экономику.',
      ),
  })

  if (query.isLoading) {
    return <LoadingShell eyebrow="Экономика" title="Загружаю баланс и ферму" cards={2} />
  }

  if (query.isError) {
    return <section className="miniapp-empty-card">{query.error.message}</section>
  }

  const economy = query.data?.economy
  if (!economy) {
    return <LoadingShell eyebrow="Экономика" title="Готовлю экран экономики" cards={2} />
  }

  return (
    <div className="miniapp-page-stack">
      <div>
        <span className="page-card__eyebrow">Экономика</span>
        <h1>Баланс: {economy.balance}</h1>
      </div>

      <article className="miniapp-empty-card">
        <p>
          Ферма: уровень {economy.farm_level}, размер «{economy.farm_size_tier}»
        </p>
        <p>
          Грядки: {economy.plots_ready} готово · {economy.plots_growing} растёт · {economy.plots_empty} пусто
        </p>
        <p>Предметов в инвентаре: {economy.inventory_item_count}</p>
        <p>
          Полная панель (ферма, рынок, крафт) пока доступна только в браузере —
          подробности через <code>/eco</code> в чате.
        </p>
      </article>
    </div>
  )
}
