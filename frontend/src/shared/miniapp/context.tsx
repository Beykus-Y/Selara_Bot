import { createContext, useContext } from 'react'

import type { MiniAppContextValue } from '@/shared/miniapp/model'

const MiniAppContext = createContext<MiniAppContextValue | null>(null)

export const MiniAppContextProvider = MiniAppContext.Provider

export function useMiniApp() {
  const value = useContext(MiniAppContext)

  if (!value) {
    throw new Error('MiniApp context is not available.')
  }

  return value
}
