export type DocsVariant = 'user' | 'admin'
export type ChatTab = 'overview' | 'achievements' | 'settings'
export type ChatSection = ChatTab | 'economy' | 'family' | 'audit'

function withChatId(path: string, chatId?: string | number | null) {
  if (chatId === undefined || chatId === null || String(chatId).trim() === '') {
    return path
  }

  return `${path}?chat_id=${encodeURIComponent(String(chatId))}`
}

export const routes = {
  landing: '/',
  login: '/login',
  userDocs: '/docs/user',
  adminDocs: '/docs/admin',
  app: '/app',
  home: '/app',
  settings: '/app/settings',
  games: '/app/games',
  achievements: '/app/achievements',
  gachaCollection: (userId: string | number) => `/app/gacha/collection/${userId}`,
  appUserDocs: '/app/docs/user',
  appAdminDocs: '/app/docs/admin',
  publicDocs: (variant: DocsVariant, chatId?: string | number | null) =>
    withChatId(variant === 'admin' ? '/docs/admin' : '/docs/user', chatId),
  appDocs: (variant: DocsVariant, chatId?: string | number | null) =>
    withChatId(variant === 'admin' ? '/app/docs/admin' : '/app/docs/user', chatId),
  chat: (chatId: string | number) => `/app/chat/${chatId}`,
  chatTab: (chatId: string | number, tab: ChatTab) => `/app/chat/${chatId}?tab=${tab}`,
  economy: (chatId: string | number) => `/app/chat/${chatId}/economy`,
  family: (chatId: string | number) => `/app/family/${chatId}`,
  audit: (chatId: string | number) => `/app/chat/${chatId}/audit`,
  feedback: '/app/feedback',
  adminLogin: '/admin/login',
  admin: '/admin',
  adminBroadcast: (broadcastId: string | number) => `/admin/broadcasts/${broadcastId}`,
  adminTable: (tableName: string) => `/admin/table/${tableName}`,
  adminTableEdit: (tableName: string, pkQuery: string) => `/admin/table/${tableName}/edit?${pkQuery}`,
}

export const appNavigation = [
  {
    label: 'Главная',
    description: 'Группы, права и обзор аккаунта',
    shortLabel: 'Домой',
    to: routes.home,
  },
  {
    label: 'Игры',
    description: 'Лобби, live-сцены и архив партий',
    shortLabel: 'Игры',
    to: routes.games,
  },
  {
    label: 'Достижения',
    description: 'Глобальный каталог и прогресс аккаунта',
    shortLabel: 'Ачивки',
    to: routes.achievements,
  },
  {
    label: 'Справка',
    description: 'Пользовательская документация и сценарии',
    shortLabel: 'Справка',
    to: routes.appUserDocs,
  },
] as const

export function buildChatSectionLinks(
  chatId: string | number,
  {
    active,
    canManageSettings = true,
  }: {
    active: ChatSection
    canManageSettings?: boolean
  },
) {
  const links = [
    { label: 'Обзор', to: routes.chatTab(chatId, 'overview'), isActive: active === 'overview' },
    { label: 'Достижения', to: routes.chatTab(chatId, 'achievements'), isActive: active === 'achievements' },
    { label: 'Экономика', to: routes.economy(chatId), isActive: active === 'economy' },
    { label: 'Моя семья', to: routes.family(chatId), isActive: active === 'family' },
    { label: 'Аудит', to: routes.audit(chatId), isActive: active === 'audit' },
  ]

  if (canManageSettings) {
    links.splice(2, 0, {
      label: 'Настройки',
      to: routes.chatTab(chatId, 'settings'),
      isActive: active === 'settings',
    })
  }

  return links
}
