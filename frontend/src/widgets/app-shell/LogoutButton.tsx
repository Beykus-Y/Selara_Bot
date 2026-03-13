import { useMutation } from '@tanstack/react-query'

import { redirectToServerPath } from '@/shared/lib/resolve-server-redirect'
import { notifySessionChanged } from '@/shared/lib/session-sync'
import { logoutSession } from '@/widgets/app-shell/api/logout-session'

type LogoutButtonProps = {
  className: string
}

export function LogoutButton({ className }: LogoutButtonProps) {
  const logoutMutation = useMutation({
    mutationFn: logoutSession,
    onSuccess: (result) => {
      notifySessionChanged()
      redirectToServerPath(result.redirect)
    },
  })

  return (
    <button
      type="button"
      className={className}
      onClick={() => {
        logoutMutation.mutate()
      }}
      disabled={logoutMutation.isPending}
    >
      {logoutMutation.isPending ? 'Выхожу…' : 'Выйти'}
    </button>
  )
}
