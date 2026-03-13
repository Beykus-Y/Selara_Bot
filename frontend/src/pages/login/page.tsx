import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useSearchParams } from 'react-router-dom'

import { getLoginContext } from '@/pages/login/api/get-login-context'
import { resolveAppPath } from '@/shared/config/app-base-path'
import { routes } from '@/shared/config/routes'
import { usePageTitle } from '@/shared/lib/use-page-title'

export function LoginPage() {
  const [searchParams] = useSearchParams()
  const [code, setCode] = useState('')
  const flashMessage = searchParams.get('flash')
  const errorMessage = searchParams.get('error')
  const loginContextQuery = useQuery({
    queryKey: ['login-context'],
    queryFn: getLoginContext,
    retry: false,
  })
  const botUsername = loginContextQuery.data?.bot_username
  const botDmUrl = loginContextQuery.data?.bot_dm_url

  usePageTitle('Вход')

  return (
    <section className="public-page">
      <div className="public-login public-login--auth">
        <section className="public-card public-card--hero">
          <span className="page-card__eyebrow">Вход в Selara</span>
          <h1>Веб-панель без пароля и отдельной регистрации</h1>
          <p className="public-copy">
            Доступ выдаётся только через Telegram-бота
            {botUsername ? (
              <>
                {' '}
                <a href={botDmUrl} target="_blank" rel="noreferrer">
                  @{botUsername}
                </a>
              </>
            ) : (
              ' Selara'
            )}
            . Бот присылает одноразовый шестизначный код, после чего браузер открывает обычную сессию.
          </p>
          <div className="public-login__pills">
            <span className="public-login__pill">одноразовый код</span>
            <span className="public-login__pill">сессия браузера</span>
            <span className="public-login__pill">вход через Telegram</span>
          </div>
        </section>

        <div className="public-login__grid">
          <article className="public-card">
            <span className="page-card__eyebrow">Как войти</span>
            <h2>Три коротких шага</h2>
            <div className="public-login__steps">
              <div className="public-login__step">
                <strong>01</strong>
                <p>
                  Откройте личный чат с ботом
                  {botUsername ? (
                    <>
                      {' '}
                      <a href={botDmUrl} target="_blank" rel="noreferrer">
                        @{botUsername}
                      </a>
                    </>
                  ) : null}
                  .
                </p>
              </div>
              <div className="public-login__step">
                <strong>02</strong>
                <p>Отправьте команду `/login` и получите новый шестизначный код. Старый код автоматически перестанет быть полезным.</p>
              </div>
              <div className="public-login__step">
                <strong>03</strong>
                <p>Введите код ниже. Он одноразовый, живёт несколько минут и не заменяет Telegram-сессию.</p>
              </div>
            </div>
            <div className="public-actions">
              {botDmUrl ? (
                <a className="button button--primary" href={botDmUrl} target="_blank" rel="noreferrer">
                  Открыть бота
                </a>
              ) : null}
              <a className="button button--secondary" href={resolveAppPath(routes.landing)}>
                На главную
              </a>
            </div>
          </article>

          <article className="public-card">
            <span className="page-card__eyebrow">Код доступа</span>
            <h2>Вход по коду из Telegram</h2>
            <p className="public-copy">
              Если код истёк или был использован, просто запросите новый у бота. Пароль на сайте хранить не нужно.
            </p>

            {flashMessage ? <div className="public-message public-message--ok">{flashMessage}</div> : null}
            {errorMessage ? <div className="public-message public-message--error">{errorMessage}</div> : null}
            {loginContextQuery.isError ? (
              <div className="public-message public-message--error">{loginContextQuery.error.message}</div>
            ) : null}

            <form
              className="public-form"
              method="post"
              action={resolveAppPath('/backend/login')}
            >
              <label className="public-field">
                <span>Код из Telegram</span>
                <input
                  className="public-code-input"
                  type="text"
                  name="code"
                  inputMode="numeric"
                  pattern="[0-9]*"
                  maxLength={6}
                  autoComplete="one-time-code"
                  autoFocus
                  placeholder="000000"
                  value={code}
                  onChange={(event) => setCode(event.target.value.replace(/\D+/g, '').slice(0, 6))}
                  required
                />
              </label>
              <button className="button button--primary" type="submit" disabled={code.length !== 6}>
                Войти
              </button>
            </form>

            <div className="public-login__support">
              <article className="public-support-card">
                <strong>После входа</strong>
                <p>Откроется кабинет с группами, статистикой, играми, экономикой, документацией и контекстной навигацией.</p>
              </article>
              <article className="public-support-card">
                <strong>Если доступа нет</strong>
                <p>Страница группы откроется только если бот видит вашу активность или роль в этом чате.</p>
              </article>
            </div>
          </article>
        </div>
      </div>
    </section>
  )
}
