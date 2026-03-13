import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link, NavLink, Outlet, matchPath, useLocation } from 'react-router-dom'

import { appNavigation, buildChatSectionLinks, routes, type ChatSection, type ChatTab } from '@/shared/config/routes'
import { getAppViewer } from '@/widgets/app-shell/api/get-app-viewer'
import { LogoutButton } from '@/widgets/app-shell/LogoutButton'
import { ProfileMenu } from '@/widgets/app-shell/ProfileMenu'

type ShellLink = {
  label: string
  to: string
}

type ShellContext = {
  eyebrow: string
  title: string
  note: string
  chips: string[]
  links: ShellLink[]
}

function deriveChatTab(search: string): ChatTab {
  const tab = new URLSearchParams(search).get('tab')

  if (tab === 'achievements' || tab === 'settings') {
    return tab
  }

  return 'overview'
}

function deriveShellContext(pathname: string, search: string): ShellContext {
  const adminDocsMatch = matchPath('/app/docs/admin', pathname)
  const userDocsMatch = matchPath('/app/docs/user', pathname)
  const chatMatch = matchPath('/app/chat/:chatId', pathname)
  const economyMatch = matchPath('/app/chat/:chatId/economy', pathname)
  const auditMatch = matchPath('/app/chat/:chatId/audit', pathname)
  const familyMatch = matchPath('/app/family/:chatId', pathname)
  const chatId =
    auditMatch?.params.chatId ??
    economyMatch?.params.chatId ??
    familyMatch?.params.chatId ??
    chatMatch?.params.chatId
  const docsChatId = new URLSearchParams(search).get('chat_id')

  if (matchPath('/app/games/*', pathname)) {
    return {
      eyebrow: 'Selara Party',
      title: 'Игровой центр',
      note: 'Лобби, live-сцены и архив последних партий работают поверх тех же серверных сценариев, что и native-панель.',
      chips: ['Живые партии', 'Telegram и браузер', 'Единый сервер'],
      links: [
        { label: 'Справка игрока', to: routes.appDocs('user') },
        { label: 'Кабинет', to: routes.home },
      ],
    }
  }

  if (matchPath('/app/achievements', pathname)) {
    return {
      eyebrow: 'Аккаунт',
      title: 'Глобальные достижения',
      note: 'Каталог достижений и прогресс аккаунта собраны в одном рабочем контуре без отдельного экранного набора.',
      chips: ['Глобальный прогресс', 'Статусы и коллекции', 'Web + Telegram'],
      links: [{ label: 'К группам', to: routes.home }],
    }
  }

  if (matchPath('/app/settings', pathname)) {
    return {
      eyebrow: 'Профиль',
      title: 'Настройки панели',
      note: 'Здесь живут настройки текущей веб-сессии, профиль и быстрые переходы по рабочим разделам панели.',
      chips: ['Сессия браузера', 'Профиль Telegram', 'React shell'],
      links: [
        { label: 'Документация', to: routes.appDocs('user') },
        { label: 'Главная', to: routes.home },
      ],
    }
  }

  if (adminDocsMatch || userDocsMatch) {
    const variant = adminDocsMatch ? 'admin' : 'user'
    return {
      eyebrow: 'Документация',
      title: variant === 'admin' ? 'Админ-справка' : 'Пользовательская справка',
      note: 'React-клиент использует те же якоря и серверные разделы документации, поэтому ссылки безопасны для native и web.',
      chips: [variant === 'admin' ? 'Настройки и роли' : 'Команды и сценарии', 'Якоря совместимы', 'Route-safe ссылки'],
      links: docsChatId
        ? [
            { label: 'Вернуться в чат', to: routes.chat(docsChatId) },
            { label: 'Главная', to: routes.home },
          ]
        : [{ label: 'Главная', to: routes.home }],
    }
  }

  if (chatId) {
    let active: ChatSection = 'overview'
    let title = 'Обзор группы'
    let note = 'Статистика, роли, настройки и сервисные разделы этой группы держатся в одном рабочем пространстве.'

    if (economyMatch) {
      active = 'economy'
      title = 'Экономика группы'
      note = 'Ферма, рынок и инвентарь работают в отдельной рабочей зоне, но остаются внутри общего контекста чата.'
    } else if (auditMatch) {
      active = 'audit'
      title = 'Журнал аудита'
      note = 'Хронология действий админов и бота остаётся привязанной к чату и доступна без ухода в native-панель.'
    } else if (familyMatch) {
      active = 'family'
      title = 'Семейный граф'
      note = 'Граф отношений и окно родственников рендерятся внутри того же chat workspace с сохранением серверного поведения.'
    } else {
      active = deriveChatTab(search)
      title =
        active === 'settings'
          ? 'Настройки группы'
          : active === 'achievements'
            ? 'Достижения группы'
            : 'Обзор группы'
      note =
        active === 'settings'
          ? 'Настройки, алиасы, смарт-триггеры и аудит сведены в один редактор без потери native-совместимости.'
          : active === 'achievements'
            ? 'Локальные достижения и прогресс этой группы обновляются отдельно от глобального аккаунта.'
            : note
    }

    const contextLinks = buildChatSectionLinks(chatId, { active, canManageSettings: true }).slice(0, 3)

    return {
      eyebrow: 'Рабочая область чата',
      title,
      note,
      chips: [`Чат #${chatId}`, 'Контекстный shell', 'Те же backend routes'],
      links: [
        ...contextLinks.map((item) => ({ label: item.label, to: item.to })),
        { label: 'Документация', to: routes.appDocs('admin', chatId) },
      ],
    }
  }

  return {
    eyebrow: 'Панель Selara',
    title: 'Управление и статистика',
    note: 'React-версия работает параллельно с native-панелью, использует те же серверные маршруты и расширяет интерфейс без смены контракта.',
    chips: ['Единый backend', 'Адаптивный shell', 'Безопасные ссылки'],
    links: [
      { label: 'Справка', to: routes.appDocs('user') },
      { label: 'Игры', to: routes.games },
    ],
  }
}

export function AppShell() {
  const location = useLocation()
  const [isDrawerOpen, setIsDrawerOpen] = useState(false)
  const viewerQuery = useQuery({
    queryKey: ['app-viewer'],
    queryFn: getAppViewer,
    retry: false,
  })

  const context = useMemo(
    () => deriveShellContext(location.pathname, location.search),
    [location.pathname, location.search],
  )
  const handleNavigationClick = () => {
    setIsDrawerOpen(false)
  }

  const navigation = (
    <>
      <div className="app-brand">
        <span className="app-brand__mark" />
        <div>
          <strong>Selara</strong>
          <p>панель управления</p>
        </div>
      </div>

      <div className="app-sidebar__section">
        <span className="app-sidebar__label">Основная навигация</span>
        <nav className="app-nav" aria-label="Основная навигация">
          {appNavigation.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              className={({ isActive }) =>
                isActive ? 'app-nav__link app-nav__link--active' : 'app-nav__link'
              }
              end={item.to === routes.home}
              onClick={handleNavigationClick}
            >
              <strong>{item.label}</strong>
              <span>{item.description}</span>
            </NavLink>
          ))}
        </nav>
      </div>

      <div className="app-sidebar__section">
        <span className="app-sidebar__label">Контекст раздела</span>
        <div className="app-sidebar__quick-links">
          {context.links.map((item) => (
            <Link key={`${context.title}-${item.to}`} className="app-quick-link" to={item.to} onClick={handleNavigationClick}>
              {item.label}
            </Link>
          ))}
        </div>
      </div>
    </>
  )

  return (
    <div className="app-shell">
      <aside className="app-sidebar">
        <div className="app-sidebar__content">{navigation}</div>

        <div className="app-sidebar__footer">
          {viewerQuery.data ? (
            <ProfileMenu viewer={viewerQuery.data} />
          ) : viewerQuery.isError ? (
            <LogoutButton className="button button--secondary app-sidebar__logout" />
          ) : (
            <div className="app-profile app-profile--loading">
              <span className="app-profile__avatar app-profile__avatar--placeholder" />
              <div className="app-profile__meta">
                <strong>Загружаю профиль…</strong>
                <span>сессия браузера</span>
              </div>
            </div>
          )}
        </div>
      </aside>

      <div className="app-content">
        <header className="app-topbar">
          <div className="app-topbar__main">
            <button
              type="button"
              className="app-topbar__menu-button"
              onClick={() => setIsDrawerOpen(true)}
              aria-label="Открыть навигацию"
            >
              <span />
              <span />
              <span />
            </button>

            <div className="app-topbar__copy">
              <span className="app-topbar__eyebrow">{context.eyebrow}</span>
              <h1>{context.title}</h1>
              <p className="app-topbar__note">{context.note}</p>
            </div>
          </div>

          <div className="app-topbar__side">
            <div className="app-topbar__chips">
              {context.chips.map((chip) => (
                <span key={`${context.title}-${chip}`} className="app-shell-chip">
                  {chip}
                </span>
              ))}
            </div>
            <div className="app-topbar__actions">
              {context.links.slice(0, 2).map((item, index) => (
                <Link
                  key={`topbar-${item.to}`}
                  className={index === 0 ? 'button button--primary' : 'button button--secondary'}
                  to={item.to}
                  onClick={handleNavigationClick}
                >
                  {item.label}
                </Link>
              ))}
            </div>
          </div>
        </header>

        <main className="app-main">
          <Outlet />
        </main>
      </div>

      <div
        className={isDrawerOpen ? 'app-drawer app-drawer--open' : 'app-drawer'}
        aria-hidden={!isDrawerOpen}
      >
        <button
          type="button"
          className="app-drawer__backdrop"
          aria-label="Закрыть навигацию"
          onClick={() => setIsDrawerOpen(false)}
        />
        <div className="app-drawer__panel">
          <div className="app-drawer__header">
            <span className="page-card__eyebrow">Навигация</span>
            <button type="button" className="button button--secondary" onClick={() => setIsDrawerOpen(false)}>
              Закрыть
            </button>
          </div>
          <div className="app-drawer__content">{navigation}</div>
        </div>
      </div>
    </div>
  )
}
