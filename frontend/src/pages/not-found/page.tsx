import { Link } from 'react-router-dom'

import { routes } from '@/shared/config/routes'
import { usePageTitle } from '@/shared/lib/use-page-title'

export function NotFoundPage() {
  usePageTitle('404')

  return (
    <section className="public-page">
      <div className="public-card">
        <span className="page-card__eyebrow">404</span>
        <h1>Страница не найдена</h1>
        <p>Маршрут не существует или ещё не добавлен в веб-клиент.</p>
        <Link className="button button--primary" to={routes.app}>
          Вернуться в панель
        </Link>
      </div>
    </section>
  )
}
