import { useState } from 'react'

import { useMiniApp } from '@/shared/miniapp/context'
import { routes } from '@/shared/config/routes'
import { usePageTitle } from '@/shared/lib/use-page-title'

export function MorePage() {
  const { viewer, miniappUrl, logout } = useMiniApp()
  const [isLoggingOut, setIsLoggingOut] = useState(false)
  const [logoutError, setLogoutError] = useState<string | null>(null)

  usePageTitle('Ещё')

  return (
    <div className="miniapp-page-stack">
      <section className="miniapp-hero-card">
        <span className="miniapp-hero-card__eyebrow">Профиль</span>
        <div className="miniapp-hero-card__headline">
          <div>
            <h1>{viewer.display_name}</h1>
            <p>{viewer.username ? `@${viewer.username}` : 'Telegram-аккаунт'}</p>
          </div>
          {viewer.avatar_url ? <img className="miniapp-profile-avatar" src={viewer.avatar_url} alt={viewer.display_name} /> : null}
        </div>
      </section>

      <div className="miniapp-more-grid">
        <article className="miniapp-more-card">
          <strong>Профиль</strong>
          <p>Сессия создаётся из Telegram initData и привязана к вашему аккаунту.</p>
        </article>

        <article className="miniapp-more-card">
          <strong>Справка</strong>
          <p>Расширенная документация и продвинутые сценарии доступны в полной панели.</p>
          <div className="miniapp-more-card__actions">
            <a className="button button--secondary" href={routes.desktopUserDocs} target="_blank" rel="noreferrer">
              Открыть справку
            </a>
            <a className="button button--secondary" href={miniappUrl}>
              Открыть в Telegram
            </a>
          </div>
        </article>

        <article className="miniapp-more-card">
          <strong>Полная панель</strong>
          <p>Настройки, журнал, достижения, экономика и администрирование — в веб-панели.</p>
          <div className="miniapp-more-card__actions">
            <a className="button button--primary" href={routes.desktop} target="_blank" rel="noreferrer">
              Перейти в панель
            </a>
          </div>
        </article>

        <article className="miniapp-more-card">
          <strong>Выход</strong>
          <p>Завершить текущую сессию мини-приложения на этом устройстве.</p>
          <div className="miniapp-more-card__actions">
            <button
              className="button button--secondary"
              type="button"
              disabled={isLoggingOut}
              onClick={async () => {
                setLogoutError(null)
                setIsLoggingOut(true)
                try {
                  await logout()
                } catch (error) {
                  setLogoutError(error instanceof Error ? error.message : 'Не удалось завершить сессию.')
                } finally {
                  setIsLoggingOut(false)
                }
              }}
            >
              {isLoggingOut ? 'Выхожу…' : 'Выйти'}
            </button>
          </div>
          {logoutError ? <p>{logoutError}</p> : null}
        </article>
      </div>
    </div>
  )
}
