import { getUserCollection } from '@/shared/api/gachaClient'
import type { CollectionResponse } from '@/shared/api/gachaClient'

export async function getGachaCollection(banner: string): Promise<CollectionResponse> {
  return getUserCollection(banner)
}
