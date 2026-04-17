export {}

declare global {
  interface TelegramThemeParams {
    bg_color?: string
    secondary_bg_color?: string
    section_bg_color?: string
    text_color?: string
    hint_color?: string
    link_color?: string
    button_color?: string
    button_text_color?: string
    accent_text_color?: string
    destructive_text_color?: string
    header_bg_color?: string
    bottom_bar_bg_color?: string
    section_header_text_color?: string
    section_separator_color?: string
    subtitle_text_color?: string
  }

  interface TelegramWebApp {
    initData: string
    colorScheme?: 'light' | 'dark'
    platform?: string
    version?: string
    themeParams: TelegramThemeParams
    ready(): void
    expand(): void
    onEvent?(eventType: 'themeChanged' | 'viewportChanged', handler: () => void): void
    offEvent?(eventType: 'themeChanged' | 'viewportChanged', handler: () => void): void
  }

  interface Window {
    Telegram?: {
      WebApp?: TelegramWebApp
    }
  }
}
