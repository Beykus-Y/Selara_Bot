import type { PropsWithChildren } from 'react'
import { useEffect, useMemo, useRef, useState } from 'react'

import { http } from '@/shared/api/http'
import { resolveAppPath } from '@/shared/config/app-base-path'
import { MiniAppContextProvider } from '@/shared/miniapp/context'
import type { MiniAppContextValue, MiniAppSessionData } from '@/shared/miniapp/model'
import { postMiniAppData } from '@/shared/miniapp/api'
import { notifySessionChanged } from '@/shared/lib/session-sync'

type LaunchStatus = 'loading' | 'blocked' | 'error' | 'logged_out' | 'ready'

type LaunchState = {
  status: LaunchStatus
  miniappUrl: string
  message?: string
  viewer?: MiniAppContextValue['viewer']
}

type LandingContextResponse = {
  ok: true
  page: {
    hero_ctas: Array<{
      href: string
      label: string
      variant: string
    }>
  }
} | {
  ok: false
  message: string
}

const MINIAPP_LOGOUT_FLAG = 'selara:miniapp-logout'

function resolveLaunchUrl(botHref: string | undefined): string {
  if (!botHref) {
    return 'https://t.me'
  }

  try {
    const parsed = new URL(botHref)
    const botUsername = parsed.pathname.replace(/^\/+/, '').split('/')[0]
    if (!botUsername) {
      return botHref
    }
    return `https://t.me/${botUsername}`
  } catch {
    return botHref
  }
}

async function loadMiniAppLaunchUrl(): Promise<string> {
  try {
    const { data } = await http.get<LandingContextResponse>('/landing/context', {
      validateStatus: () => true,
    })

    if (!data.ok) {
      return 'https://t.me'
    }

    const telegramCta = data.page.hero_ctas.find((item) => item.href.startsWith('https://t.me/'))
    return resolveLaunchUrl(telegramCta?.href)
  } catch {
    return 'https://t.me'
  }
}

function normalizeViewer(viewer: MiniAppContextValue['viewer']) {
  return {
    ...viewer,
    avatar_url: resolveAppPath(viewer.avatar_url),
  }
}

function applyTelegramTheme(webApp: TelegramWebApp) {
  const root = document.documentElement
  const theme = webApp.themeParams || {}

  document.body.classList.add('miniapp-mode')
  root.style.setProperty('--miniapp-bg', theme.bg_color || '#07131d')
  root.style.setProperty('--miniapp-surface', theme.secondary_bg_color || theme.section_bg_color || '#122234')
  root.style.setProperty('--miniapp-surface-strong', theme.header_bg_color || '#17324a')
  root.style.setProperty('--miniapp-text', theme.text_color || '#f4f7fb')
  root.style.setProperty('--miniapp-text-muted', theme.hint_color || theme.subtitle_text_color || '#9db2c7')
  root.style.setProperty('--miniapp-accent', theme.button_color || theme.link_color || '#68a8ff')
  root.style.setProperty('--miniapp-accent-strong', theme.accent_text_color || theme.button_color || '#8bc4ff')
  root.style.setProperty('--miniapp-border', theme.section_separator_color || 'rgba(255, 255, 255, 0.08)')
  root.style.setProperty('--miniapp-pill', theme.bottom_bar_bg_color || 'rgba(14, 25, 36, 0.86)')
  root.dataset.tgColorScheme = webApp.colorScheme || 'dark'
}

function MiniAppStatusScreen({
  title,
  text,
  miniappUrl,
  actionLabel,
  onAction,
}: {
  title: string
  text: string
  miniappUrl: string
  actionLabel: string
  onAction?: (() => void) | null
}) {
  return (
    <div className="miniapp-launch">
      <section className="miniapp-launch__card">
        <span className="miniapp-launch__eyebrow">Telegram Mini App</span>
        <h1>{title}</h1>
        <p>{text}</p>
        <div className="miniapp-launch__actions">
          {onAction ? (
            <button className="button button--primary" type="button" onClick={onAction}>
              {actionLabel}
            </button>
          ) : (
            <a className="button button--primary" href={miniappUrl}>
              {actionLabel}
            </a>
          )}
          <a className="button button--secondary" href="/app" target="_blank" rel="noreferrer">
            Открыть ПК-панель
          </a>
        </div>
      </section>
    </div>
  )
}

export function MiniAppGate({ children }: PropsWithChildren) {
  const bootRef = useRef(false)
  const [state, setState] = useState<LaunchState>({
    status: 'loading',
    miniappUrl: 'https://t.me',
  })

  useEffect(() => {
    document.body.classList.add('miniapp-mode')
    return () => {
      document.body.classList.remove('miniapp-mode')
    }
  }, [])

  useEffect(() => {
    if (bootRef.current) {
      return
    }

    bootRef.current = true
    let disposed = false
    let cleanupTheme: (() => void) | undefined

    async function boot() {
      const miniappUrl = await loadMiniAppLaunchUrl()
      if (disposed) {
        return
      }

      if (window.sessionStorage.getItem(MINIAPP_LOGOUT_FLAG) === '1') {
        setState({
          status: 'logged_out',
          miniappUrl,
          message: 'Сессия miniapp завершена на этом устройстве.',
        })
        return
      }

      const webApp = window.Telegram?.WebApp
      const initData = webApp?.initData?.trim() || ''

      if (!webApp || !initData) {
        setState({
          status: 'blocked',
          miniappUrl,
          message: 'Этот клиент открывается только из Telegram Mini App. Во внешнем браузере рабочий shell отключён.',
        })
        return
      }

      const syncTheme = () => {
        applyTelegramTheme(webApp)
      }

      syncTheme()
      webApp.ready()
      webApp.expand()
      webApp.onEvent?.('themeChanged', syncTheme)
      cleanupTheme = () => {
        webApp.offEvent?.('themeChanged', syncTheme)
      }

      try {
        const payload = await postMiniAppData<MiniAppSessionData>(
          '/miniapp/session',
          { init_data: initData },
          'Не удалось открыть miniapp-сессию.',
        )

        if (disposed) {
          return
        }

        notifySessionChanged()
        setState({
          status: 'ready',
          miniappUrl: payload.miniapp_url || miniappUrl,
          viewer: normalizeViewer(payload.viewer),
        })
      } catch (error) {
        if (disposed) {
          return
        }

        setState({
          status: 'error',
          miniappUrl,
          message: error instanceof Error ? error.message : 'Не удалось запустить miniapp.',
        })
      }
    }

    void boot()

    return () => {
      disposed = true
      cleanupTheme?.()
    }
  }, [])

  const contextValue = useMemo<MiniAppContextValue | null>(() => {
    if (state.status !== 'ready' || !state.viewer) {
      return null
    }

    return {
      viewer: state.viewer,
      miniappUrl: state.miniappUrl,
      logout: async () => {
        await postMiniAppData('/miniapp/logout', {}, 'Не удалось завершить miniapp-сессию.')
        window.sessionStorage.setItem(MINIAPP_LOGOUT_FLAG, '1')
        notifySessionChanged()
        setState((current) => ({
          status: 'logged_out',
          miniappUrl: current.miniappUrl,
          message: 'Сессия miniapp завершена на этом устройстве.',
        }))
      },
      reopen: () => {
        window.sessionStorage.removeItem(MINIAPP_LOGOUT_FLAG)
        window.location.reload()
      },
    }
  }, [state])

  if (state.status === 'loading') {
    return (
      <div className="miniapp-launch">
        <section className="miniapp-launch__card">
          <span className="miniapp-launch__eyebrow">Telegram Mini App</span>
          <h1>Запускаю Selara</h1>
          <p>Проверяю Telegram initData, поднимаю сессию и готовлю мобильный shell.</p>
        </section>
      </div>
    )
  }

  if (state.status === 'blocked') {
    return (
      <MiniAppStatusScreen
        title="Откройте в Telegram"
        text={state.message || 'Этот miniapp работает только внутри Telegram.'}
        miniappUrl={state.miniappUrl}
        actionLabel="Открыть в Telegram"
      />
    )
  }

  if (state.status === 'error') {
    return (
      <MiniAppStatusScreen
        title="Mini App не запустился"
        text={state.message || 'Telegram не подтвердил запуск miniapp.'}
        miniappUrl={state.miniappUrl}
        actionLabel="Открыть заново в Telegram"
      />
    )
  }

  if (state.status === 'logged_out') {
    return (
      <MiniAppStatusScreen
        title="Сессия завершена"
        text={state.message || 'Miniapp сессия остановлена. Чтобы войти снова, откройте клиент повторно.'}
        miniappUrl={state.miniappUrl}
        actionLabel="Войти снова"
        onAction={() => {
          window.sessionStorage.removeItem(MINIAPP_LOGOUT_FLAG)
          window.location.reload()
        }}
      />
    )
  }

  return <MiniAppContextProvider value={contextValue}>{children}</MiniAppContextProvider>
}
