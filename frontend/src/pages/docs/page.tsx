import { useQuery } from '@tanstack/react-query'
import { useSearchParams } from 'react-router-dom'

import { getAdminDocsPage } from '@/pages/docs/api/get-admin-docs-page'
import { getUserDocsPage } from '@/pages/docs/api/get-user-docs-page'
import { AdminDocsPageView } from '@/pages/docs/ui/AdminDocsPageView'
import { UserDocsPageView } from '@/pages/docs/ui/UserDocsPageView'

type DocsPageProps = {
  variant: 'user' | 'admin'
}

export function DocsPage({ variant }: DocsPageProps) {
  const [searchParams] = useSearchParams()
  const chatId = searchParams.get('chat_id')

  const userDocsQuery = useQuery({
    queryKey: ['user-docs-page', chatId],
    queryFn: () => getUserDocsPage(chatId),
    enabled: variant === 'user',
  })

  const adminDocsQuery = useQuery({
    queryKey: ['admin-docs-page', chatId],
    queryFn: () => getAdminDocsPage(chatId),
    enabled: variant === 'admin',
  })

  if (variant === 'admin') {
    if (adminDocsQuery.isLoading) {
      return <section className="docs-loading">Загружаю документацию администратора…</section>
    }

    if (adminDocsQuery.isError) {
      return <section className="docs-error">{adminDocsQuery.error.message}</section>
    }

    if (!adminDocsQuery.data) {
      return <section className="docs-loading">Документация администратора пока недоступна.</section>
    }

    return <AdminDocsPageView data={adminDocsQuery.data} />
  }

  if (userDocsQuery.isLoading) {
    return <section className="docs-loading">Загружаю пользовательскую документацию…</section>
  }

  if (userDocsQuery.isError) {
    return <section className="docs-error">{userDocsQuery.error.message}</section>
  }

  if (!userDocsQuery.data) {
    return <section className="docs-loading">Документация пока недоступна.</section>
  }

  return <UserDocsPageView data={userDocsQuery.data} />
}
