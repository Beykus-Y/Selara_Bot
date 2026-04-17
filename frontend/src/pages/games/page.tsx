import { useMutation, useQuery } from '@tanstack/react-query'
import { useMemo, useState } from 'react'

import { useGamesLiveRefresh } from '@/pages/games/lib/use-games-live-refresh'
import { GamesPageView } from '@/pages/games/ui/GamesPageView'
import { usePageTitle } from '@/shared/lib/use-page-title'
import { getMiniAppPage, postMiniAppData } from '@/shared/miniapp/api'
import type { MiniAppGamesPageData } from '@/shared/miniapp/model'
import { LoadingShell } from '@/shared/ui/LoadingShell'

export function GamesPage() {
  const [feedbackMessage, setFeedbackMessage] = useState<string | null>(null)
  usePageTitle('Games')

  const gamesQuery = useQuery({
    queryKey: ['miniapp-games-page'],
    queryFn: () => getMiniAppPage<MiniAppGamesPageData>('/miniapp/games', 'Не удалось загрузить экран игр.'),
  })

  const liveGameIds = useMemo(
    () => gamesQuery.data?.game_cards.filter((item) => item.status_badge === 'active').map((item) => item.game_id) ?? [],
    [gamesQuery.data?.game_cards],
  )

  const gameActionMutation = useMutation({
    mutationFn: (values: Record<string, string | number | boolean | null | undefined>) =>
      postMiniAppData<{ message: string }>(
        '/miniapp/games/action',
        values,
        'Не удалось выполнить игровое действие.',
      ),
    onSuccess: async (message) => {
      setFeedbackMessage(message.message)
      await gamesQuery.refetch()
    },
  })

  useGamesLiveRefresh({
    enabled: true,
    gameIds: liveGameIds,
    onRefresh: () => {
      void gamesQuery.refetch()
    },
  })

  if (gamesQuery.isLoading) {
    return <LoadingShell eyebrow="Games" title="Поднимаю live-карточки" />
  }

  if (gamesQuery.isError) {
    return <section className="miniapp-empty-card">{gamesQuery.error.message}</section>
  }

  if (!gamesQuery.data) {
    return <LoadingShell eyebrow="Games" title="Подгружаю текущие партии" />
  }

  return (
    <GamesPageView
      data={gamesQuery.data}
      isRefreshing={gamesQuery.isFetching}
      feedbackMessage={feedbackMessage}
      isMutating={gameActionMutation.isPending}
      onRefresh={() => {
        void gamesQuery.refetch()
      }}
      onCreateGame={async (_payload) => {}}
      onGameAction={async (payload) => {
        setFeedbackMessage(null)
        await gameActionMutation.mutateAsync(payload)
      }}
    />
  )
}
