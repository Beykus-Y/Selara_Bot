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
  appUserDocs: '/app/docs/user',
  appAdminDocs: '/app/docs/admin',
  chat: (chatId: string | number) => `/app/chat/${chatId}`,
  economy: (chatId: string | number) => `/app/chat/${chatId}/economy`,
  family: (chatId: string | number) => `/app/family/${chatId}`,
  audit: (chatId: string | number) => `/app/chat/${chatId}/audit`,
} as const

export const appNavigation = [
  { label: 'Главная', to: routes.home },
  { label: 'Игры', to: routes.games },
  { label: 'Достижения', to: routes.achievements },
  { label: 'Справка', to: routes.userDocs },
] as const
