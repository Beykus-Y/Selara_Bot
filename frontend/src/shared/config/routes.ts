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
  desktop: '/app',
  desktopGames: '/app/games',
  desktopUserDocs: '/app/docs/user',
  desktopAdminDocs: '/app/docs/admin',
  desktopChat: (chatId: string | number) => `/app/chat/${chatId}`,

  home: '/',
  groups: '/groups',
  games: '/games',
  gacha: '/gacha',
  more: '/more',

  settings: '/more',
  achievements: '/more',
  gachaCollection: (_userId: string | number) => '/gacha',
  appUserDocs: '/app/docs/user',
  appAdminDocs: '/app/docs/admin',
  publicDocs: (variant: DocsVariant, chatId?: string | number | null) =>
    withChatId(variant === 'admin' ? '/docs/admin' : '/docs/user', chatId),
  appDocs: (variant: DocsVariant, chatId?: string | number | null) =>
    withChatId(variant === 'admin' ? '/app/docs/admin' : '/app/docs/user', chatId),
  chat: (chatId: string | number) => `/chat/${chatId}`,
  chatTab: (chatId: string | number, tab: ChatTab) => `/chat/${chatId}?tab=${tab}`,
  economy: (chatId: string | number) => `/chat/${chatId}/economy`,
  family: (chatId: string | number) => `/family/${chatId}`,
  audit: (chatId: string | number) => `/chat/${chatId}/audit`,
  feedback: '/feedback',

  adminLogin: '/admin/login',
  admin: '/admin',
  adminBroadcast: (broadcastId: string | number) => `/admin/broadcasts/${broadcastId}`,
  adminTable: (tableName: string) => `/admin/table/${tableName}`,
  adminTableEdit: (tableName: string, pkQuery: string) => `/admin/table/${tableName}/edit?${pkQuery}`,
}

export const miniappNavigation = [
  {
    label: 'Home',
    description: 'Сводка аккаунта и быстрые входы',
    shortLabel: 'Home',
    to: routes.home,
  },
  {
    label: 'Groups',
    description: 'Группы с доступом и активностью',
    shortLabel: 'Groups',
    to: routes.groups,
  },
  {
    label: 'Games',
    description: 'Live-игры и архив партий',
    shortLabel: 'Games',
    to: routes.games,
  },
  {
    label: 'Gacha',
    description: 'Коллекция, профиль и recent pulls',
    shortLabel: 'Gacha',
    to: routes.gacha,
  },
  {
    label: 'More',
    description: 'Профиль, help, desktop и logout',
    shortLabel: 'More',
    to: routes.more,
  },
] as const

export const appNavigation = miniappNavigation

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
