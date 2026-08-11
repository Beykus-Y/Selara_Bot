import { createContext } from 'react'

import type { MiniAppContextValue } from '@/shared/miniapp/model'

export const MiniAppContext = createContext<MiniAppContextValue | null>(null)
export const MiniAppContextProvider = MiniAppContext.Provider
