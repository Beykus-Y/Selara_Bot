import { useEffect } from 'react'
import { Link, isRouteErrorResponse, useRouteError } from 'react-router-dom'

import { routes } from '@/shared/config/routes'

function getErrorCopy(error: unknown) {
  if (isRouteErrorResponse(error)) {
    return {
      title: error.status === 404 ? 'Страница не найдена' : 'Страница временно недоступна',
      note: typeof error.data === 'string' ? error.data : error.statusText,
    }
  }

  if (error instanceof Error) {
    return {
      title: 'Интерфейс столкнулся с ошибкой',
      note: error.message,
    }
  }

  return {
    title: 'Интерфейс столкнулся с ошибкой',
    note: 'Попробуйте обновить страницу или вернуться в рабочий раздел панели.',
  }
}

export function RouteErrorBoundary() {
  const error = useRouteError()
  const copy = getErrorCopy(error)

  useEffect(() => {
    document.title = 'Ошибка • Selara'
  }, [])

  return (
    <section className="route-error">
      <div className="route-error__card">
        <span className="page-card__eyebrow">Ошибка маршрута</span>
        <h1>{copy.title}</h1>
        <p>{copy.note}</p>
        <div className="route-error__actions">
          <Link className="button button--primary" to={routes.home}>
            Вернуться в кабинет
          </Link>
          <Link className="button button--secondary" to={routes.appUserDocs}>
            Открыть справку
          </Link>
        </div>
      </div>
    </section>
  )
}
