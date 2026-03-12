import { Link } from 'react-router-dom'

import { routes } from '@/shared/config/routes'

export function LandingPage() {
  return (
    <section className="public-page">
      <div className="public-card public-card--hero">
        <span className="page-card__eyebrow">Selara</span>
        <h1>Новая веб-панель Selara</h1>
        <p>
          Браузерный кабинет для управления группами, играми, экономикой и документацией Selara.
        </p>
        <div className="public-actions">
          <Link className="button button--primary" to={routes.app}>
            Открыть приложение
          </Link>
          <Link className="button" to={routes.login}>
            Вход
          </Link>
        </div>
      </div>
    </section>
  )
}
