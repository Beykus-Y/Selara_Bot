import { postForm } from '@/shared/api/post-form'
import { handleApiFailure } from '@/shared/lib/api-response'

import type { AdminLoginResponse } from '@/pages/admin-login/model/types'

export async function adminLogin(password: string): Promise<{ message: string }> {
  try {
    const data = await postForm<AdminLoginResponse>('/api/admin/login', { password })

    if (!data.ok) {
      throw new Error(data.message)
    }

    return { message: data.message }
  } catch (error) {
    handleApiFailure(error, 'Не удалось выполнить вход в админку.')
  }
}
