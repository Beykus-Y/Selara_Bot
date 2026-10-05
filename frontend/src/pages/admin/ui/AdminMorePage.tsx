import { Link } from 'react-router-dom'

import { routes } from '@/shared/config/routes'

export function AdminMorePage() {
  return (
    <section className="admin-page">
      <header className="admin-page__heading">
        <p className="admin-eyebrow">Служебные действия</p>
        <h1>Ещё</h1>
      </header>
      <nav className="admin-more-list" aria-label="Дополнительные действия">
        <Link to={routes.adminAi}><span><strong>AI и монетизация</strong><small>Расходы AI, Stars, платежи и подписки</small></span><span aria-hidden="true">›</span></Link>
        <Link to={routes.adminBroadcast}><span><strong>Рассылка</strong><small>Создать отправку и посмотреть прогресс</small></span><span aria-hidden="true">›</span></Link>
        <a href="/app/admin"><span><strong>Старая админ-панель</strong><small>Desktop и fallback интерфейс</small></span><span aria-hidden="true">↗</span></a>
        <a href="/app/admin#operations"><span><strong>Настройки operational alerts</strong><small>Получатель и включение уведомлений</small></span><span aria-hidden="true">↗</span></a>
        <p>Selara Mini App Admin · Telegram session</p>
      </nav>
    </section>
  )
}
