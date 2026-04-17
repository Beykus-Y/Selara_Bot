import { useState } from 'react'

import { useMiniApp } from '@/shared/miniapp/context'
import { routes } from '@/shared/config/routes'
import { usePageTitle } from '@/shared/lib/use-page-title'

export function MorePage() {
  const { viewer, miniappUrl, logout } = useMiniApp()
  const [isLoggingOut, setIsLoggingOut] = useState(false)
  const [logoutError, setLogoutError] = useState<string | null>(null)

  usePageTitle('More')

  return (
    <div className="miniapp-page-stack">
      <section className="miniapp-hero-card">
        <span className="miniapp-hero-card__eyebrow">More</span>
        <div className="miniapp-hero-card__headline">
          <div>
            <h1>{viewer.display_name}</h1>
            <p>{viewer.username || `Telegram ID ${viewer.telegram_user_id}`}</p>
          </div>
          {viewer.avatar_url ? <img className="miniapp-profile-avatar" src={viewer.avatar_url} alt={viewer.display_name} /> : null}
        </div>
      </section>

      <div className="miniapp-more-grid">
        <article className="miniapp-more-card">
          <strong>Viewer profile</strong>
          <p>Miniapp использует обычную web-session, но создаёт её строго из Telegram initData.</p>
        </article>

        <article className="miniapp-more-card">
          <strong>Help and docs</strong>
          <p>Расширенная справка и продвинутые сценарии остаются в desktop `/app`.</p>
          <div className="miniapp-more-card__actions">
            <a className="button button--secondary" href={routes.desktopUserDocs} target="_blank" rel="noreferrer">
              Docs
            </a>
            <a className="button button--secondary" href={miniappUrl}>
              Telegram link
            </a>
          </div>
        </article>

        <article className="miniapp-more-card">
          <strong>Desktop panel</strong>
          <p>Settings, audit, achievements, economy и админка остаются в Python/Jinja-панели.</p>
          <div className="miniapp-more-card__actions">
            <a className="button button--primary" href={routes.desktop} target="_blank" rel="noreferrer">
              Открыть `/app`
            </a>
          </div>
        </article>

        <article className="miniapp-more-card">
          <strong>Logout</strong>
          <p>Остановить текущую miniapp-сессию на этом устройстве.</p>
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
              {isLoggingOut ? 'Завершаю…' : 'Выйти'}
            </button>
          </div>
          {logoutError ? <p>{logoutError}</p> : null}
        </article>
      </div>
    </div>
  )
}
