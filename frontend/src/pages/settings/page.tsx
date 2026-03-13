import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'

import { routes } from '@/shared/config/routes'
import { usePageTitle } from '@/shared/lib/use-page-title'
import { LoadingShell } from '@/shared/ui/LoadingShell'
import { PanelGlyph } from '@/shared/ui/PanelGlyph'
import { LogoutButton } from '@/widgets/app-shell/LogoutButton'
import { getAppViewer } from '@/widgets/app-shell/api/get-app-viewer'

import './ui/settings-page.css'

const actionCards = [
  {
    title: 'Главная панель',
    text: 'Вернуться к списку групп, прав и глобальной статистике аккаунта.',
    href: routes.home,
    label: 'Открыть кабинет',
    icon: 'chat' as const,
  },
  {
    title: 'Справка',
    text: 'Пользовательская документация по командам, играм и сценариям Selara.',
    href: routes.appDocs('user'),
    label: 'Открыть документацию',
    icon: 'docs' as const,
  },
  {
    title: 'Игры',
    text: 'Лобби, live-сцены и архив последних партий из браузера.',
    href: routes.games,
    label: 'Перейти в игры',
    icon: 'gamepad' as const,
  },
]

export function SettingsPage() {
  const viewerQuery = useQuery({
    queryKey: ['app-viewer'],
    queryFn: getAppViewer,
  })
  usePageTitle('Настройки панели')

  if (viewerQuery.isLoading) {
    return <LoadingShell eyebrow="Настройки" title="Поднимаю профиль панели" cards={3} />
  }

  if (viewerQuery.isError) {
    return <section className="page-card">{viewerQuery.error.message}</section>
  }

  if (!viewerQuery.data) {
    return <LoadingShell eyebrow="Настройки" title="Профиль панели пока недоступен" cards={3} />
  }

  const viewer = viewerQuery.data
  const profileFields = [
    {
      label: 'Telegram ID',
      value: String(viewer.telegram_user_id),
      note: 'Основной идентификатор аккаунта.',
    },
    {
      label: 'Username',
      value: viewer.username || 'не указан',
      note: 'Используется для быстрых упоминаний и ссылок.',
    },
    {
      label: 'Имя',
      value: viewer.first_name || '—',
      note: 'Основное имя из Telegram-профиля.',
    },
    {
      label: 'Фамилия',
      value: viewer.last_name || '—',
      note: 'Дополнительное поле профиля, если задано.',
    },
  ]

  return (
    <div className="settings-page">
      <section className="settings-hero">
        <div className="settings-hero__profile">
          <div className="settings-avatar">
            {viewer.avatar_url ? <img src={viewer.avatar_url} alt="" /> : <span>{viewer.initials}</span>}
          </div>
          <div>
            <span className="page-card__eyebrow">Профиль веб-панели</span>
            <h1>{viewer.display_name}</h1>
            <p>Текущая браузерная сессия привязана к Telegram-аккаунту и использует те же права доступа, что и native-панель.</p>
          </div>
        </div>
        <div className="settings-hero__actions">
          <Link className="button button--primary" to={routes.home}>
            К группам
          </Link>
          <LogoutButton className="button button--secondary" />
        </div>
      </section>

      <section className="settings-grid">
        <article className="settings-panel">
          <div className="settings-panel__head">
            <div>
              <h2>Данные аккаунта</h2>
              <p>Сводка Telegram-профиля, под которым открыта текущая панель.</p>
            </div>
          </div>
          <div className="settings-field-grid">
            {profileFields.map((field) => (
              <label key={field.label} className="settings-field">
                <span>{field.label}</span>
                <input type="text" readOnly value={field.value} />
                <small>{field.note}</small>
              </label>
            ))}
          </div>
        </article>

        <article className="settings-panel">
          <div className="settings-panel__head">
            <div>
              <h2>Сессия и доступ</h2>
              <p>Панель не хранит отдельный пароль и не дублирует серверную авторизацию.</p>
            </div>
          </div>
          <div className="settings-list">
            <div className="settings-list-row">
              <strong>Вход только через Telegram</strong>
              <p>Новый доступ выдаётся одноразовым кодом из `/login` у бота.</p>
            </div>
            <div className="settings-list-row">
              <strong>Права доступа общие с backend</strong>
              <p>React-shell и native-панель используют один и тот же серверный слой разрешений.</p>
            </div>
            <div className="settings-list-row">
              <strong>Выход завершает браузерную сессию</strong>
              <p>После logout потребуется запросить новый код, если старая сессия уже истекла.</p>
            </div>
          </div>
        </article>
      </section>

      <section className="settings-actions-grid">
        {actionCards.map((card) => (
          <article key={card.title} className="settings-panel settings-action-card">
            <div className="settings-panel__head">
              <span className="settings-action-card__icon">
                <PanelGlyph kind={card.icon} />
              </span>
              <div>
                <h2>{card.title}</h2>
                <p>{card.text}</p>
              </div>
            </div>
            <Link className="button button--secondary" to={card.href}>
              {card.label}
            </Link>
          </article>
        ))}
      </section>
    </div>
  )
}
