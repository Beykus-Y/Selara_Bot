import { useQuery } from '@tanstack/react-query'

import { getChatAiAccess } from '@/pages/chat/api/get-chat-ai-access'

/** Shared by the page (to start loading in parallel with the overview) and the panel. */
export function useChatAiAccess(chatId: string | undefined) {
  return useQuery({
    queryKey: ['miniapp-chat-ai-access', chatId],
    queryFn: ({ signal }) => getChatAiAccess(chatId as string, { signal }),
    enabled: Boolean(chatId),
    staleTime: 15_000,
  })
}
