import { postForm } from '@/shared/api/post-form'
import { handleApiFailure } from '@/shared/lib/api-response'
import { redirectToServerPath } from '@/shared/lib/resolve-server-redirect'

type ActionResponse = {
  ok: boolean
  message: string
  redirect?: string
}

async function submitEconomyAction(
  url: string,
  values: Record<string, string | number | boolean | null | undefined>,
) {
  try {
    const data = await postForm<ActionResponse>(url, values)

    if (!data.ok) {
      redirectToServerPath(data.redirect)
      throw new Error(data.message)
    }

    return data.message
  } catch (error) {
    handleApiFailure(error, 'Не удалось выполнить действие экономики.')
  }
}

export function applyEconomyItem(
  chatId: string,
  values: { item_code: string; target_type: string; plot_no?: number | null },
) {
  return submitEconomyAction(`/api/chat/${chatId}/economy/apply`, values)
}

export function createMarketListing(
  chatId: string,
  values: { item_code: string; quantity: number; unit_price: number },
) {
  return submitEconomyAction(`/api/chat/${chatId}/economy/market/create`, values)
}

export function buyMarketListing(
  chatId: string,
  values: { listing_id: number; quantity: number },
) {
  return submitEconomyAction(`/api/chat/${chatId}/economy/market/buy`, values)
}

export function cancelMarketListing(chatId: string, listingId: number) {
  return submitEconomyAction(`/api/chat/${chatId}/economy/market/cancel`, {
    listing_id: listingId,
  })
}
