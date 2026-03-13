import { useMutation, useQuery } from '@tanstack/react-query'
import { useMemo, useState } from 'react'

import { createGame, runGameAction } from '@/pages/games/api/actions'
import { getGamesPage } from '@/pages/games/api/get-games-page'
import { useGamesLiveRefresh } from '@/pages/games/lib/use-games-live-refresh'
import { GamesPageView } from '@/pages/games/ui/GamesPageView'
import { usePageTitle } from '@/shared/lib/use-page-title'
import { LoadingShell } from '@/shared/ui/LoadingShell'

export function GamesPage() {
  const [feedbackMessage, setFeedbackMessage] = useState<string | null>(null)
  usePageTitle('Игры')
  const gamesQuery = useQuery({
    queryKey: ['games-page'],
    queryFn: getGamesPage,
  })
  const liveGameIds = useMemo(
    () => gamesQuery.data?.game_cards.filter((item) => item.status_badge === 'active').map((item) => item.game_id) ?? [],
    [gamesQuery.data?.game_cards],
  )

  const createGameMutation = useMutation({
    mutationFn: createGame,
    onSuccess: async (message) => {
      setFeedbackMessage(message)
      await gamesQuery.refetch()
    },
  })

  const gameActionMutation = useMutation({
    mutationFn: runGameAction,
    onSuccess: async (message) => {
      setFeedbackMessage(message)
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
    return <LoadingShell eyebrow="Игры" title="Поднимаю игровой центр" />
  }

  if (gamesQuery.isError) {
    return <section className="games-error">{gamesQuery.error.message}</section>
  }

  if (!gamesQuery.data) {
    return <LoadingShell eyebrow="Игры" title="Подгружаю live-сцены" />
  }

  return (
    <GamesPageView
      data={gamesQuery.data}
      isRefreshing={gamesQuery.isFetching}
      feedbackMessage={feedbackMessage}
      isMutating={createGameMutation.isPending || gameActionMutation.isPending}
      onRefresh={() => {
        void gamesQuery.refetch()
      }}
      onCreateGame={async (payload) => {
        setFeedbackMessage(null)
        await createGameMutation.mutateAsync(payload)
      }}
      onGameAction={async (payload) => {
        setFeedbackMessage(null)
        await gameActionMutation.mutateAsync(payload)
      }}
    />
  )
}
