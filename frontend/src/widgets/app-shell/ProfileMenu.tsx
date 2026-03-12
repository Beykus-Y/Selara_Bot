import { useEffect, useRef, useState } from 'react'
import { Link, useLocation } from 'react-router-dom'

import { routes } from '@/shared/config/routes'
import type { AppViewer } from '@/widgets/app-shell/model/types'

type ProfileMenuProps = {
  viewer: AppViewer
}

export function ProfileMenu({ viewer }: ProfileMenuProps) {
  const location = useLocation()
  const [isOpen, setIsOpen] = useState(false)
  const [avatarFailed, setAvatarFailed] = useState(false)
  const rootRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    if (!isOpen) {
      return
    }

    const handlePointerDown = (event: MouseEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) {
        setIsOpen(false)
      }
    }

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setIsOpen(false)
      }
    }

    window.addEventListener('mousedown', handlePointerDown)
    window.addEventListener('keydown', handleKeyDown)

    return () => {
      window.removeEventListener('mousedown', handlePointerDown)
      window.removeEventListener('keydown', handleKeyDown)
    }
  }, [isOpen])

  return (
    <div ref={rootRef} className="app-profile">
      <button
        type="button"
        className={isOpen ? 'app-profile__trigger app-profile__trigger--open' : 'app-profile__trigger'}
        onClick={() => setIsOpen((value) => !value)}
      >
        <span className="app-profile__avatar" aria-hidden="true">
          {!avatarFailed ? (
            <img
              src={viewer.avatar_url}
              alt=""
              onError={() => setAvatarFailed(true)}
            />
          ) : (
            <span>{viewer.initials}</span>
          )}
        </span>
        <span className="app-profile__meta">
          <strong>{viewer.display_name}</strong>
          <span>{viewer.username || `ID ${viewer.telegram_user_id}`}</span>
        </span>
      </button>

      {isOpen ? (
        <div className="app-profile__menu">
          <Link
            className={location.pathname === routes.settings ? 'app-profile__menu-item app-profile__menu-item--active' : 'app-profile__menu-item'}
            to={routes.settings}
            onClick={() => setIsOpen(false)}
          >
            Настройки
          </Link>
          <form method="post" action="/logout">
            <button type="submit" className="app-profile__menu-item">
              Выйти
            </button>
          </form>
        </div>
      ) : null}
    </div>
  )
}
