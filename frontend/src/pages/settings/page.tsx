import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'

import { resolveAppPath } from '@/shared/config/app-base-path'
import { routes } from '@/shared/config/routes'
import { getAppViewer } from '@/widgets/app-shell/api/get-app-viewer'

export function SettingsPage() {
  const viewerQuery = useQuery({
    queryKey: ['app-viewer'],
    queryFn: getAppViewer,
  })

  if (viewerQuery.isLoading) {
    return <section className="page-card">Загружаю настройки панели…</section>
  }

  if (viewerQuery.isError) {
    return <section className="page-card">{viewerQuery.error.message}</section>
  }

  if (!viewerQuery.data) {
    return <section className="page-card">Профиль панели пока недоступен.</section>
  }

  const viewer = viewerQuery.data

  return (
    <section className="page-card">
      <div className="page-card__header">
        <span className="page-card__eyebrow">Профиль</span>
        <h1>Настройки панели</h1>
        <p>Пока здесь собран профиль текущей сессии браузера. Личные настройки интерфейса можно добавить следующим этапом.</p>
      </div>

      <div className="page-card__content">
        <h2>{viewer.display_name}</h2>
        <ul>
          <li>Telegram ID: {viewer.telegram_user_id}</li>
          <li>Username: {viewer.username || 'не указан'}</li>
          <li>Имя: {[viewer.first_name, viewer.last_name].filter(Boolean).join(' ') || 'не заполнено'}</li>
        </ul>
      </div>

      <div className="public-actions">
        <Link className="button button--primary" to={routes.userDocs}>
          Справка
        </Link>
        <form method="post" action={resolveAppPath('/logout')}>
          <button type="submit" className="button">
            Выйти
          </button>
        </form>
      </div>
    </section>
  )
}
