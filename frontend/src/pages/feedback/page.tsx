import { useQuery } from '@tanstack/react-query'

import { getFeedbackPage } from '@/pages/feedback/api/get-feedback-page'
import { FeedbackPageView } from '@/pages/feedback/ui/FeedbackPageView'
import { usePageTitle } from '@/shared/lib/use-page-title'

export function FeedbackPage() {
  const query = useQuery({
    queryKey: ['feedback-page'],
    queryFn: getFeedbackPage,
  })
  usePageTitle('Обратная связь')

  if (query.isLoading) {
    return <section className="feedback-loading">Загружаю заявки…</section>
  }

  if (query.isError) {
    return <section className="feedback-page-error">{query.error.message}</section>
  }

  if (!query.data) {
    return <section className="feedback-loading">Данные пока недоступны.</section>
  }

  return <FeedbackPageView data={query.data} />
}
