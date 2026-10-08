import { useState } from 'react'
import { Link } from 'react-router-dom'

import { useMiniApp } from '@/shared/miniapp/use-miniapp'
import { routes } from '@/shared/config/routes'
import { usePageTitle } from '@/shared/lib/use-page-title'
import { PanelGlyph } from '@/shared/ui/PanelGlyph'

export function MorePage() {
  const { viewer, miniappUrl, logout, permissions } = useMiniApp()
  const [isLoggingOut, setIsLoggingOut] = useState(false)
  const [logoutError, setLogoutError] = useState<string | null>(null)

  usePageTitle('Профиль')

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
    <div className="v2">
      <h1 className="v2-title">Профиль</h1>
      <div style={{ marginTop: 18, display: 'flex', gap: 14, alignItems: 'center' }}>
        <span className="v2-ava" style={{ width: 52, height: 52, overflow: 'hidden' }}>
          {viewer.avatar_url ? (
            <img src={viewer.avatar_url} alt={viewer.display_name} style={{ width: '100%', height: '100%', objectFit: 'cover' }} />
          ) : (
            initials
          )}
        </span>
        <span className="v2-main">
          <b style={{ fontSize: 16 }}>{viewer.display_name}</b>
          <span>{viewer.username ? `@${viewer.username}` : 'Telegram-аккаунт'}</span>
        </span>
      </div>

      <div className="v2-sec"><span>Разделы</span></div>
      <Link className="v2-row v2-row--nav" style={{ marginTop: 4 }} to={routes.personal}>
        <span className="v2-ico"><PanelGlyph kind="spark" /></span>
        <span className="v2-label">Моя Selara</span>
        <span className="v2-chev">›</span>
      </Link>
      {permissions.admin && (
        <Link className="v2-row v2-row--nav" to={routes.admin}>
          <span className="v2-ico"><PanelGlyph kind="settings" /></span>
          <span className="v2-label">Админ-панель</span>
          <span className="v2-chev">›</span>
        </Link>
      )}
      <a className="v2-row v2-row--nav" href={routes.desktop} target="_blank" rel="noreferrer">
        <span className="v2-ico"><PanelGlyph kind="docs" /></span>
        <span className="v2-label">Открыть полную версию в браузере</span>
        <span className="v2-chev">↗</span>
      </a>
      <a className="v2-row v2-row--nav" href={routes.desktopUserDocs} target="_blank" rel="noreferrer">
        <span className="v2-ico"><PanelGlyph kind="docs" /></span>
        <span className="v2-label">Справка и сценарии</span>
        <span className="v2-chev">↗</span>
      </a>
      <a className="v2-row v2-row--nav" href={miniappUrl} target="_blank" rel="noreferrer">
        <span className="v2-ico"><PanelGlyph kind="telegram" /></span>
        <span className="v2-label">Открыть бота в Telegram</span>
        <span className="v2-chev">↗</span>
      </a>

      <div className="v2-sec"><span>Аккаунт</span></div>
      <p className="v2-muted" style={{ margin: '14px 0 0', fontSize: 13 }}>Язык интерфейса: русский</p>
      <button type="button" className="v2-row v2-row--danger" onClick={handleLogout} disabled={isLoggingOut}>
        {isLoggingOut ? 'Выхожу…' : 'Выйти из сессии'}
      </button>

      {logoutError && <div className="v2-error" role="alert"><span>{logoutError}</span></div>}
    </div>
  )
}
