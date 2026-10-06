import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'

import { forgetAllPersonalData } from '@/pages/personal/api/personal-api'

export function PrivacySection() {
  const queryClient = useQueryClient()
  const [confirming, setConfirming] = useState(false)
  const [result, setResult] = useState<string | null>(null)

  const forget = useMutation({
    mutationFn: forgetAllPersonalData,
    onSuccess: ({ removed }) => {
      setConfirming(false)
      setResult(`Всё удалено: ${removed.messages} сообщ., ${removed.memories} фактов.`)
      return queryClient.invalidateQueries({ queryKey: ['miniapp-personal'] })
    },
    onError: () => setResult(null),
  })

  return (
    <section className="miniapp-section-card personal-privacy" aria-labelledby="personal-privacy-title">
      <div className="miniapp-section-head">
        <h2 id="personal-privacy-title">Приватность</h2>
      </div>
      <p className="selara-ai__note">
        Можно стереть профиль и настройки, историю диалогов и ролевой игры, резюме и всю память. Подписка и платежи не
        затрагиваются. Это нельзя отменить. В уже сделанных резервных копиях бота эти данные могут сохраняться, пока копии не будут удалены.
      </p>
      {!confirming ? (
        <button className="button button--danger" type="button" onClick={() => { setResult(null); setConfirming(true) }}>
          Удалить все мои данные
        </button>
      ) : (
        <div className="personal-confirm" role="alertdialog" aria-labelledby="personal-confirm-title">
          <strong id="personal-confirm-title">Удалить все личные данные Selara AI?</strong>
          <div className="personal-confirm__actions">
            <button className="button button--danger" type="button" disabled={forget.isPending} onClick={() => forget.mutate()}>
              {forget.isPending ? 'Удаляю…' : 'Да, удалить всё'}
            </button>
            <button className="button button--secondary" type="button" disabled={forget.isPending} onClick={() => setConfirming(false)}>
              Отмена
            </button>
          </div>
        </div>
      )}
      {forget.isError && (
        <p className="personal-notice is-error" role="alert">
          {forget.error.message}
        </p>
      )}
      {result && (
        <p className="personal-notice is-ok" role="status">
          {result}
        </p>
      )}
    </section>
  )
}
