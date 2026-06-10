import { useState } from 'react'

import { useMiniApp } from '@/shared/miniapp/context'
import { routes } from '@/shared/config/routes'
import { usePageTitle } from '@/shared/lib/use-page-title'

export function MorePage() {
  const { viewer, miniappUrl, logout } = useMiniApp()
  const [isLoggingOut, setIsLoggingOut] = useState(false)
  const [logoutError, setLogoutError] = useState<string | null>(null)

  usePageTitle('Ещё')

  const handleLogout = async () => {
    if (isLoggingOut) return
    setLogoutError(null)
    setIsLoggingOut(true)
    try {
      await logout()
    } catch (error) {
      setLogoutError(error instanceof Error ? error.message : 'Не удалось завершить сессию.')
    } finally {
      setIsLoggingOut(false)
    }
  }

  const initials = viewer.initials || viewer.display_name.slice(0, 2)

  return (
    <div className="miniapp-page-stack">
      {/* Title */}
      <div>
        <div className="eyebrow">Ещё</div>
        <h1 className="page">Профиль</h1>
        <div className="page-sub">Аккаунт, ссылки и помощь</div>
      </div>

      {/* Mini Profile Card */}
      <div className="card">
        <div className="more-profile" style={{ marginBottom: 0 }}>
          <div className="avatar-ring" style={{ width: '52px', height: '52px' }}>
            <div className="avatar" style={{ inset: 0, fontSize: '17px' }}>
              {viewer.avatar_url ? (
                <img src={viewer.avatar_url} alt={viewer.display_name} />
              ) : (
                initials
              )}
            </div>
          </div>
          <div>
            <div className="profile-name" style={{ fontSize: '15px' }}>
              {viewer.display_name}
            </div>
            <div className="profile-handle">
              {viewer.username ? `@${viewer.username}` : 'Telegram-аккаунт'}
            </div>
          </div>
        </div>
      </div>

      {/* Navigation section */}
      <h2 className="sec">Навигация</h2>
      <div className="card link-list" style={{ padding: '4px 14px' }}>
        <a className="link" href={routes.desktop} target="_blank" rel="noreferrer">
          <div className="ico">🖥️</div>
          <b>Полная панель (ПК)</b>
          <span>›</span>
        </a>
        <a className="link" href={routes.desktopUserDocs} target="_blank" rel="noreferrer">
          <div className="ico">📖</div>
          <b>Справка и сценарии</b>
          <span>›</span>
        </a>
        <a className="link" href={miniappUrl} target="_blank" rel="noreferrer">
          <div className="ico">💬</div>
          <b>Открыть бота в Telegram</b>
          <span>›</span>
        </a>
      </div>

      {/* Account Section */}
      <h2 className="sec">Аккаунт</h2>
      <div className="card link-list" style={{ padding: '4px 14px' }}>
        <div className="link">
          <div className="ico">🌐</div>
          <b>Язык — русский</b>
          <span>›</span>
        </div>
        <div className="link" onClick={handleLogout} style={{ cursor: 'pointer' }}>
          <div className="ico">🚪</div>
          <b>{isLoggingOut ? 'Выхожу…' : 'Выйти из сессии'}</b>
          <span>›</span>
        </div>
      </div>

      {logoutError && (
        <div
          style={{
            background: 'rgba(248, 113, 113, 0.1)',
            color: 'var(--red)',
            padding: '10px',
            borderRadius: '8px',
            fontSize: '12.5px',
            textAlign: 'center',
            marginTop: '10px',
            border: '1px solid var(--red)',
          }}
        >
          {logoutError}
        </div>
      )}

      <div className="version">SELARA MINI APP · v2.0 · PRODUCTION</div>
    </div>
  )
}
