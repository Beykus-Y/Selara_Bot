import axios from 'axios'
import { http } from '@/shared/api/http'
import { handleApiFailure } from '@/shared/lib/api-response'
import { redirectToServerPath } from '@/shared/lib/resolve-server-redirect'
import { resolveAppPath } from '@/shared/config/app-base-path'

import type {
  AdminTablePageData,
  AdminTablePageResponse,
  AdminTableEditPageData,
  AdminTableEditPageResponse,
  AdminTableActionResponse,
} from '@/pages/admin-table/model/types'

export async function getAdminTablePage(
  tableName: string,
  params: Record<string, string>,
): Promise<AdminTablePageData> {
  try {
    const { data } = await http.get<AdminTablePageResponse>(`/admin/table/${tableName}`, { params })

    if (!data.ok) {
      redirectToServerPath(data.redirect)
      throw new Error(data.message)
    }

    return data.page
  } catch (error) {
    handleApiFailure(error, 'Не удалось загрузить таблицу.')
  }
}

export async function getAdminTableEditPage(
  tableName: string,
  pkQuery: string,
): Promise<AdminTableEditPageData> {
  try {
    const params = Object.fromEntries(new URLSearchParams(pkQuery))
    const { data } = await http.get<AdminTableEditPageResponse>(`/admin/table/${tableName}/edit`, { params })

    if (!data.ok) {
      redirectToServerPath(data.redirect)
      throw new Error(data.message)
    }

    return data.page
  } catch (error) {
    handleApiFailure(error, 'Не удалось загрузить запись для редактирования.')
  }
}

async function postAdminForm<T>(url: string, values: Record<string, string>): Promise<T> {
  const form = new URLSearchParams(values)
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

export async function adminTableUpdate(
  tableName: string,
  fields: Record<string, string>,
): Promise<string> {
  try {
    const data = await postAdminForm<AdminTableActionResponse>(`/api/admin/table/${tableName}/update`, fields)
    if (!data.ok) throw new Error(data.message)
    return data.message
  } catch (error) {
    handleApiFailure(error, 'Не удалось обновить запись.')
  }
}

export async function adminTableDelete(
  tableName: string,
  pkFields: Record<string, string>,
): Promise<string> {
  try {
    const data = await postAdminForm<AdminTableActionResponse>(`/api/admin/table/${tableName}/delete`, pkFields)
    if (!data.ok) throw new Error(data.message)
    return data.message
  } catch (error) {
    handleApiFailure(error, 'Не удалось удалить запись.')
  }
}
