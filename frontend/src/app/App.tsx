import { AppProviders } from '@/app/providers/app-providers'
import { AppRouter } from '@/app/router/AppRouter'

export function App() {
  return (
    <AppProviders>
      <AppRouter />
    </AppProviders>
  )
}
