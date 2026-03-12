import { useMutation, useQuery } from '@tanstack/react-query'
import { useParams } from 'react-router-dom'

import {
  applyEconomyItem,
  buyMarketListing,
  cancelMarketListing,
  createMarketListing,
} from '@/pages/economy/api/actions'
import { getEconomyPage } from '@/pages/economy/api/get-economy-page'
import { EconomyPageView } from '@/pages/economy/ui/EconomyPageView'
import { useNamedEventSource } from '@/shared/lib/use-named-event-source'
import { useState } from 'react'

const ECONOMY_LIVE_EVENT_NAMES = ['chat_refresh'] as const

export function EconomyPage() {
  const { chatId } = useParams()
  const [feedbackMessage, setFeedbackMessage] = useState<string | null>(null)

  const economyQuery = useQuery({
    queryKey: ['economy-page', chatId],
    queryFn: () => getEconomyPage(chatId!),
    enabled: Boolean(chatId),
  })

  const applyMutation = useMutation({
    mutationFn: (payload: { item_code: string; target_type: string; plot_no?: number | null }) =>
      applyEconomyItem(chatId!, payload),
    onSuccess: async (message) => {
      setFeedbackMessage(message)
      await economyQuery.refetch()
    },
  })

  const createListingMutation = useMutation({
    mutationFn: (payload: { item_code: string; quantity: number; unit_price: number }) =>
      createMarketListing(chatId!, payload),
    onSuccess: async (message) => {
      setFeedbackMessage(message)
      await economyQuery.refetch()
    },
  })

  const buyMutation = useMutation({
    mutationFn: (payload: { listing_id: number; quantity: number }) => buyMarketListing(chatId!, payload),
    onSuccess: async (message) => {
      setFeedbackMessage(message)
      await economyQuery.refetch()
    },
  })

  const cancelMutation = useMutation({
    mutationFn: (listingId: number) => cancelMarketListing(chatId!, listingId),
    onSuccess: async (message) => {
      setFeedbackMessage(message)
      await economyQuery.refetch()
    },
  })

  useNamedEventSource({
    enabled: Boolean(chatId),
    path: `/api/live/stream?scope=chat&chat_id=${chatId}`,
    eventNames: ECONOMY_LIVE_EVENT_NAMES,
    onEvent: () => {
      void economyQuery.refetch()
    },
  })

  if (!chatId) {
    return <section className="economy-error">Не удалось определить ID чата.</section>
  }

  if (economyQuery.isLoading) {
    return <section className="economy-loading">Загружаю экономику…</section>
  }

  if (economyQuery.isError) {
    return <section className="economy-error">{economyQuery.error.message}</section>
  }

  if (!economyQuery.data) {
    return <section className="economy-loading">Данные экономики пока недоступны.</section>
  }

  return (
    <EconomyPageView
      chatId={chatId}
      data={economyQuery.data}
      feedbackMessage={feedbackMessage}
      isMutating={
        applyMutation.isPending ||
        createListingMutation.isPending ||
        buyMutation.isPending ||
        cancelMutation.isPending
      }
      onApplyItem={async (payload) => {
        setFeedbackMessage(null)
        await applyMutation.mutateAsync(payload)
      }}
      onCreateListing={async (payload) => {
        setFeedbackMessage(null)
        await createListingMutation.mutateAsync(payload)
      }}
      onBuyListing={async (payload) => {
        setFeedbackMessage(null)
        await buyMutation.mutateAsync(payload)
      }}
      onCancelListing={async (listingId) => {
        setFeedbackMessage(null)
        await cancelMutation.mutateAsync(listingId)
      }}
    />
  )
}
