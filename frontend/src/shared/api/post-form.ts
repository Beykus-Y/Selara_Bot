import axios from 'axios'
import { resolveAppPath } from '@/shared/config/app-base-path'

export async function postForm<T>(
  url: string,
  values: Record<string, string | number | boolean | null | undefined>,
): Promise<T> {
  const form = new URLSearchParams()

  for (const [key, value] of Object.entries(values)) {
    if (value === null || value === undefined) {
      continue
    }

    form.set(key, String(value))
  }

  const { data } = await axios.post<T>(resolveAppPath(url), form.toString(), {
    withCredentials: true,
    headers: {
      Accept: 'application/json',
      'Content-Type': 'application/x-www-form-urlencoded',
      'X-Requested-With': 'fetch',
    },
  })

  return data
}
