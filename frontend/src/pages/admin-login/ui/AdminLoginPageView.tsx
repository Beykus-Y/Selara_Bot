import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useMutation } from '@tanstack/react-query'

import { adminLogin } from '@/pages/admin-login/api/admin-login'
import { routes } from '@/shared/config/routes'

import './admin-login-page.css'

export function AdminLoginPageView() {
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const navigate = useNavigate()

  const loginMutation = useMutation({
    mutationFn: () => adminLogin(password),
    onSuccess: () => {
      void navigate(routes.admin)
    },
    onError: (err: Error) => {
      setError(err.message)
    },
  })

  return (
    <div className="admin-login-page">
      <section className="admin-login-hero">
        <div>
          <span className="page-card__eyebrow">Админ-панель Selara</span>
          <h1>Вход для администратора</h1>
          <p>Доступ к панели управления ботом и базой данных. Только для авторизованных администраторов.</p>
        </div>
        <div className="admin-login-side">
          <article className="admin-login-side-card">
            <span className="kicker">безопасность</span>
            <strong>Отдельная авторизация</strong>
            <p>Админ-панель использует отдельную сессию, не связанную с обычной веб-авторизацией.</p>
          </article>
          <article className="admin-login-side-card">
            <span className="kicker">доступ</span>
            <strong>Только по прямой ссылке</strong>
            <p>Ссылка на админку не публикуется в интерфейсе. Доступ только по прямому URL.</p>
          </article>
        </div>
      </section>

      <div className="admin-login-grid">
        <article className="admin-login-panel">
          <div className="admin-login-panel__head">
            <h2>Вход в админку</h2>
            <p className="admin-login-panel__subtitle">Введите пароль администратора для доступа к панели управления.</p>
          </div>

          {error && <div className="admin-login-error">{error}</div>}

          <form
            className="admin-login-form"
            onSubmit={(e) => {
              e.preventDefault()
              loginMutation.mutate()
            }}
          >
            <label className="admin-login-field">
              <span>Пароль администратора</span>
              <input
                type="password"
                autoComplete="current-password"
                autoFocus
                required
                value={password}
                onChange={(e) => setPassword(e.target.value)}
              />
            </label>
            <button type="submit" className="button primary" disabled={loginMutation.isPending}>
              {loginMutation.isPending ? 'Вхожу…' : 'Войти'}
            </button>
          </form>
          <p className="admin-login-helper">
            Пароль хранится в переменной окружения <code>ADMIN_PASSWORD</code>.
          </p>
        </article>

        <article className="admin-login-panel">
          <div className="admin-login-panel__head">
            <h2>Возможности админки</h2>
            <p className="admin-login-panel__subtitle">Просмотр и редактирование данных бота.</p>
          </div>
          <ul className="admin-login-feature-list">
            <li>📊 <strong>Просмотр таблиц БД</strong> — все основные таблицы бота</li>
            <li>🔍 <strong>Поиск и фильтрация</strong> — быстрый поиск по записям</li>
            <li>✏️ <strong>Редактирование</strong> — ручное исправление данных</li>
            <li>🗑️ <strong>Удаление</strong> — удаление проблемных записей</li>
            <li>📝 <strong>Аудит действий</strong> — лог всех изменений</li>
          </ul>
        </article>
      </div>
    </div>
  )
}
