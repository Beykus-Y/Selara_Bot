import { getUserCollection } from '@/shared/api/gachaClient'
import type { CollectionResponse } from '@/shared/api/gachaClient'

export async function getGachaCollection(userId: number, banner: string): Promise<CollectionResponse> {
  return getUserCollection(userId, banner)
}
