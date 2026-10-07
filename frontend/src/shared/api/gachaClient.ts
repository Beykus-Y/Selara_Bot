/**
 * Gacha service API client
 * Communicates with independent gacha microservice
 */

import { isAxiosError } from 'axios'

import { http } from '@/shared/api/http'
import { resolveAppPath } from '@/shared/config/app-base-path'

export interface CollectionCard {
  code: string
  name: string
  rarity: string
  rarity_label: string
  copies_owned: number
  image_url: string
}

export interface CollectionResponse {
  status: string
  banner: string
  user_id: number
  cards: CollectionCard[]
  total_unique: number
  total_copies: number
}

export interface PlayerProfile {
  user_id: number
  adventure_rank: number
  adventure_xp: number
  xp_into_rank: number
  xp_for_next_rank: number
  total_points: number
  total_primogems: number
}

export interface ProfileResponse {
  status: string
  banner: string
  message: string
  player: PlayerProfile
  unique_cards: number
  total_copies: number
  recent_pulls: Array<{
    pulled_at: string
    card_name: string
    rarity: string
    rarity_label: string
    points: number
    primogems: number
    adventure_xp_gained: number
    image_url: string
  }>
}

class GachaClientError extends Error {
  statusCode?: number

  constructor(
    message: string,
    statusCode?: number,
  ) {
    super(message)
    this.name = 'GachaClientError'
    this.statusCode = statusCode
  }
}

/**
 * Get GACHA_API_URL from environment or construct from current origin
 */
function getGachaApiUrl(): string {
  // Try environment variable first
  const envUrl = import.meta.env.VITE_GACHA_API_URL
  if (envUrl) {
    return envUrl.toString()
  }

  // Fallback to localhost:8001 for development
  // In production, this should be set via VITE_GACHA_API_URL
  if (import.meta.env.DEV) {
    return 'http://localhost:8001'
  }

  return `${window.location.origin}${resolveAppPath('/gacha')}`
}

/**
 * Public gacha proxy base. Card images (`/images/...`) stay publicly proxied at `/miniapp/gacha/`;
 * only the per-user reads go through the authenticated app API below.
 */
const GACHA_API_URL = getGachaApiUrl()

/**
 * Per-user gacha reads go through the main app (`/miniapp/api/miniapp/gacha/...`): the browser sends
 * only its Mini App session, and the server adds `X-Gacha-Service-Token` when it calls the gacha
 * service. The browser must never hold that service token, which is why these calls cannot hit the
 * gacha service directly.
 */
const APP_GACHA_API_PATH = '/miniapp/gacha'

function normalizeGachaImageUrl(imageUrl: string): string {
  const value = imageUrl.trim()
  if (!value) {
    return value
  }

  const proxyBase = GACHA_API_URL

  if (value.startsWith('/images/')) {
    return `${proxyBase}${value}`
  }

  try {
    const parsed = new URL(value)
    if (parsed.pathname.startsWith('/images/')) {
      return `${proxyBase}${parsed.pathname}${parsed.search}${parsed.hash}`
    }
  } catch {
    return value
  }

  return value
}

function normalizeCollectionResponse(payload: CollectionResponse): CollectionResponse {
  return {
    ...payload,
    cards: payload.cards.map((card) => ({
      ...card,
      image_url: normalizeGachaImageUrl(card.image_url),
    })),
  }
}

function normalizeProfileResponse(payload: ProfileResponse): ProfileResponse {
  return {
    ...payload,
    recent_pulls: payload.recent_pulls.map((entry) => ({
      ...entry,
      image_url: normalizeGachaImageUrl(entry.image_url),
    })),
  }
}

async function request<T>(
  path: string,
  params: Record<string, string | number>,
  fallback: string,
  signal?: AbortSignal,
): Promise<T> {
  try {
    const response = await http.request<T & { ok?: boolean; message?: string }>({
      method: 'get',
      url: `${APP_GACHA_API_PATH}${path}`,
      params,
      signal,
      validateStatus: () => true,
    })
    const data = response.data
    if (response.status >= 400 || data?.ok === false) {
      throw new GachaClientError(data?.message || fallback, response.status)
    }
    return data
  } catch (error) {
    if (isAxiosError(error) && error.code === 'ERR_CANCELED') throw error
    if (error instanceof GachaClientError) throw error
    throw new GachaClientError(fallback)
  }
}

/**
 * Get user collection for a specific banner
 * Returns all cards owned by user sorted by code
 */
export async function getUserCollection(banner: string = 'genshin'): Promise<CollectionResponse> {
  const payload = await request<CollectionResponse>(
    '/collection',
    { banner },
    'Не удалось загрузить коллекцию.',
  )
  return normalizeCollectionResponse(payload)
}

/**
 * Get user profile and recent pulls
 */
export async function getUserProfile(banner: string = 'genshin', limit: number = 5): Promise<ProfileResponse> {
  const payload = await request<ProfileResponse>(
    '/profile',
    { banner, limit },
    'Не удалось загрузить профиль.',
  )
  return normalizeProfileResponse(payload)
}
