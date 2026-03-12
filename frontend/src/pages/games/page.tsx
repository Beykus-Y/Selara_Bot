import { useMutation, useQuery } from '@tanstack/react-query'
import { useState } from 'react'

import { createGame, runGameAction } from '@/pages/games/api/actions'
import { getGamesPage } from '@/pages/games/api/get-games-page'
import { GamesPageView } from '@/pages/games/ui/GamesPageView'
import { useNamedEventSource } from '@/shared/lib/use-named-event-source'

const GAMES_LIVE_EVENT_NAMES = ['new_vote', 'phase_change', 'game_updated'] as const

export function GamesPage() {
  const [feedbackMessage, setFeedbackMessage] = useState<string | null>(null)
  const gamesQuery = useQuery({
    queryKey: ['games-page'],
    queryFn: getGamesPage,
    refetchInterval: 10000,
  })

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

  useNamedEventSource({
    enabled: true,
    path: '/api/live/stream?scope=games',
    eventNames: GAMES_LIVE_EVENT_NAMES,
    onEvent: () => {
      void gamesQuery.refetch()
    },
  })

  if (gamesQuery.isLoading) {
    return <section className="games-loading">Загружаю игры…</section>
  }

  if (gamesQuery.isError) {
    return <section className="games-error">{gamesQuery.error.message}</section>
  }

  if (!gamesQuery.data) {
    return <section className="games-loading">Данные игр пока недоступны.</section>
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
