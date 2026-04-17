import { AppProviders } from '@/app/providers/app-providers'
import { AppRouter } from '@/app/router/AppRouter'
import { MiniAppGate } from '@/shared/miniapp/MiniAppGate'
import '@/shared/styles/miniapp.css'

export function App() {
  return (
    <AppProviders>
      <MiniAppGate>
        <AppRouter />
      </MiniAppGate>
    </AppProviders>
  )
}
