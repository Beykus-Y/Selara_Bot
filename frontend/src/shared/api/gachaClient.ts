/**
 * Gacha service API client
 * Communicates with independent gacha microservice
 */

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

const GACHA_API_URL = getGachaApiUrl()

async function request<T>(
  method: string,
  path: string,
  options?: {
    params?: Record<string, string | number>
    headers?: Record<string, string>
  },
): Promise<T> {
  let url = `${GACHA_API_URL}${path}`

  // Add query parameters
  if (options?.params) {
    const searchParams = new URLSearchParams()
    for (const [key, value] of Object.entries(options.params)) {
      searchParams.append(key, String(value))
    }
    url += `?${searchParams.toString()}`
  }

  try {
    const response = await fetch(url, {
      method,
      headers: {
        'Content-Type': 'application/json',
        ...options?.headers,
      },
    })

    if (!response.ok) {
      const errorText = await response.text()
      throw new GachaClientError(
        `Gacha API error: ${response.statusText}. ${errorText}`,
        response.status,
      )
    }

    return response.json()
  } catch (error) {
    if (error instanceof GachaClientError) {
      throw error
    }
    throw new GachaClientError(`Failed to communicate with Gacha service at ${GACHA_API_URL}`)
  }
}

/**
 * Get user collection for a specific banner
 * Returns all cards owned by user sorted by code
 */
export async function getUserCollection(
  userId: number,
  banner: string = 'genshin',
): Promise<CollectionResponse> {
  return request<CollectionResponse>('GET', `/v1/gacha/users/${userId}/collection`, {
    params: { banner },
  })
}

/**
 * Get user profile and recent pulls
 */
export async function getUserProfile(
  userId: number,
  banner: string = 'genshin',
  limit: number = 5,
): Promise<ProfileResponse> {
  return request<ProfileResponse>('GET', `/v1/gacha/users/${userId}/profile`, {
    params: { banner, limit },
  })
}
