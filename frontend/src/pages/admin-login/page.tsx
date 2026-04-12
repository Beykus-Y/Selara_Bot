import { usePageTitle } from '@/shared/lib/use-page-title'
import { AdminLoginPageView } from '@/pages/admin-login/ui/AdminLoginPageView'

export function AdminLoginPage() {
  usePageTitle('Вход в админку')
  return <AdminLoginPageView />
}
