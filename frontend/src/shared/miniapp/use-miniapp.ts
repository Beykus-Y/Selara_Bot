import { useContext } from 'react'

import { MiniAppContext } from '@/shared/miniapp/context'

export function useMiniApp() {
  const value = useContext(MiniAppContext)
  if (!value) {
    throw new Error('MiniApp context is not available.')
  }
  return value
}
