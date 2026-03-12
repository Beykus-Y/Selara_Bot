import { useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { useParams, useSearchParams } from 'react-router-dom'

import {
  deleteChatAlias,
  deleteChatTrigger,
  saveChatAlias,
  saveChatTrigger,
} from '@/pages/chat/api/actions'
import { getChatAchievements } from '@/pages/chat/api/get-chat-achievements'
import { getChatLeaderboard } from '@/pages/chat/api/get-chat-leaderboard'
import { getChatOverview } from '@/pages/chat/api/get-chat-overview'
import { getChatSettings } from '@/pages/chat/api/get-chat-settings'
import { updateChatSetting } from '@/pages/chat/api/update-chat-setting'
import type { ChatLeaderboardMode } from '@/pages/chat/model/types'
import { ChatAchievementsView } from '@/pages/chat/ui/ChatAchievementsView'
import { ChatOverviewView } from '@/pages/chat/ui/ChatOverviewView'
import { ChatSettingsView } from '@/pages/chat/ui/ChatSettingsView'
import { useNamedEventSource } from '@/shared/lib/use-named-event-source'

type ChatTab = 'overview' | 'achievements' | 'settings'
const CHAT_LIVE_EVENT_NAMES = ['chat_activity', 'new_vote', 'chat_refresh'] as const

function normalizeTab(value: string | null): ChatTab {
  if (value === 'achievements' || value === 'settings') {
    return value
  }

  return 'overview'
}

export function ChatPage() {
  const { chatId } = useParams()
  const [searchParams] = useSearchParams()
  const activeTab = normalizeTab(searchParams.get('tab'))
  const [mode, setMode] = useState<ChatLeaderboardMode>('mix')
  const [page, setPage] = useState(1)
  const [query, setQuery] = useState('')
  const [searchValue, setSearchValue] = useState('')
  const [settingsMessage, setSettingsMessage] = useState<string | null>(null)

  const overviewQuery = useQuery({
    queryKey: ['chat-overview', chatId],
    queryFn: () => getChatOverview(chatId!),
    enabled: Boolean(chatId) && activeTab === 'overview',
  })

  const leaderboardQuery = useQuery({
    queryKey: ['chat-leaderboard', chatId, mode, page, query],
    queryFn: () =>
      getChatLeaderboard({
        chatId: chatId!,
        mode,
        page,
        query,
      }),
    enabled: Boolean(chatId) && activeTab === 'overview',
  })

  const achievementsQuery = useQuery({
    queryKey: ['chat-achievements', chatId],
    queryFn: () => getChatAchievements(chatId!),
    enabled: Boolean(chatId) && activeTab === 'achievements',
  })

  const settingsQuery = useQuery({
    queryKey: ['chat-settings', chatId],
    queryFn: () => getChatSettings(chatId!),
    enabled: Boolean(chatId) && activeTab === 'settings',
  })

  const updateSettingMutation = useMutation({
    mutationFn: ({ key, value }: { key: string; value: string }) => updateChatSetting(chatId!, key, value),
    onSuccess: async (message) => {
      setSettingsMessage(message.message)
      await settingsQuery.refetch()
    },
  })

  const saveAliasMutation = useMutation({
    mutationFn: (values: { alias_text: string; source_trigger: string }) => saveChatAlias(chatId!, values),
    onSuccess: async (message) => {
      setSettingsMessage(message)
      await settingsQuery.refetch()
    },
  })

  const deleteAliasMutation = useMutation({
    mutationFn: (aliasText: string) => deleteChatAlias(chatId!, aliasText),
    onSuccess: async (message) => {
      setSettingsMessage(message)
      await settingsQuery.refetch()
    },
  })

  const saveTriggerMutation = useMutation({
    mutationFn: (values: {
      trigger_id?: string
      keyword: string
      match_type: string
      response_text: string
      media_file_id: string
      media_type: string
    }) => saveChatTrigger(chatId!, values),
    onSuccess: async (message) => {
      setSettingsMessage(message)
      await settingsQuery.refetch()
    },
  })

  const deleteTriggerMutation = useMutation({
    mutationFn: (triggerId: string) => deleteChatTrigger(chatId!, triggerId),
    onSuccess: async (message) => {
      setSettingsMessage(message)
      await settingsQuery.refetch()
    },
  })

  useNamedEventSource({
    enabled: Boolean(chatId),
    path: `/api/live/stream?scope=chat&chat_id=${chatId}`,
    eventNames: CHAT_LIVE_EVENT_NAMES,
    onEvent: () => {
      if (activeTab === 'overview') {
        void overviewQuery.refetch()
        void leaderboardQuery.refetch()
        return
      }

      if (activeTab === 'achievements') {
        void achievementsQuery.refetch()
        return
      }

      void settingsQuery.refetch()
    },
  })

  if (!chatId) {
    return <section className="chat-error">Не удалось определить ID чата.</section>
  }

  if (activeTab === 'settings') {
    if (settingsQuery.isLoading) {
      return <section className="chat-loading">Загружаю настройки группы…</section>
    }

    if (settingsQuery.isError) {
      return <section className="chat-error">{settingsQuery.error.message}</section>
    }

    if (!settingsQuery.data) {
      return <section className="chat-loading">Данные настроек группы пока недоступны.</section>
    }

    return (
      <ChatSettingsView
        chatId={chatId}
        data={settingsQuery.data}
        feedbackMessage={settingsMessage}
        pendingKey={updateSettingMutation.variables?.key ?? null}
        isSaving={
          updateSettingMutation.isPending ||
          saveAliasMutation.isPending ||
          deleteAliasMutation.isPending ||
          saveTriggerMutation.isPending ||
          deleteTriggerMutation.isPending
        }
        onSave={async (key, value) => {
          setSettingsMessage(null)
          await updateSettingMutation.mutateAsync({ key, value })
        }}
        onSaveAlias={async (values) => {
          setSettingsMessage(null)
          await saveAliasMutation.mutateAsync(values)
        }}
        onDeleteAlias={async (aliasText) => {
          setSettingsMessage(null)
          await deleteAliasMutation.mutateAsync(aliasText)
        }}
        onSaveTrigger={async (values) => {
          setSettingsMessage(null)
          await saveTriggerMutation.mutateAsync(values)
        }}
        onDeleteTrigger={async (triggerId) => {
          setSettingsMessage(null)
          await deleteTriggerMutation.mutateAsync(triggerId)
        }}
      />
    )
  }

  if (activeTab === 'achievements') {
    if (achievementsQuery.isLoading) {
      return <section className="chat-loading">Загружаю достижения группы…</section>
    }

    if (achievementsQuery.isError) {
      return <section className="chat-error">{achievementsQuery.error.message}</section>
    }

    if (!achievementsQuery.data) {
      return <section className="chat-loading">Данные достижений группы пока недоступны.</section>
    }

    return <ChatAchievementsView chatId={chatId} data={achievementsQuery.data} />
  }

  if (overviewQuery.isLoading || leaderboardQuery.isLoading) {
    return <section className="chat-loading">Загружаю обзор группы…</section>
  }

  if (overviewQuery.isError) {
    return <section className="chat-error">{overviewQuery.error.message}</section>
  }

  if (leaderboardQuery.isError) {
    return <section className="chat-error">{leaderboardQuery.error.message}</section>
  }

  if (!overviewQuery.data || !leaderboardQuery.data) {
    return <section className="chat-loading">Данные группы пока недоступны.</section>
  }

  return (
    <ChatOverviewView
      chatId={chatId}
      activeTab={activeTab}
      overview={overviewQuery.data}
      leaderboard={leaderboardQuery.data}
      searchValue={searchValue}
      onSearchValueChange={setSearchValue}
      onSearchSubmit={() => {
        setPage(1)
        setQuery(searchValue.trim())
      }}
      onModeChange={(nextMode) => {
        setMode(nextMode)
        setPage(1)
      }}
      onPageChange={(nextPage) => {
        setPage(nextPage)
      }}
      onFindMe={() => {
        const myRank = leaderboardQuery.data?.my_rank
        const pageSize = leaderboardQuery.data?.page_size

        if (!myRank || !pageSize) {
          return
        }

        setPage(Math.ceil(myRank / pageSize))
      }}
    />
  )
}
