import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'

import { updatePersonalSettings } from '@/pages/personal/api/personal-api'
import type { PersonalOverview, PersonalSettingsPatch } from '@/pages/personal/model/types'

const QUERY_KEY = ['miniapp-personal']

export function ToolsSection({ data }: { data: PersonalOverview }) {
  const queryClient = useQueryClient()
  const { profile } = data
  const [error, setError] = useState<string | null>(null)
  const settings = useMutation({
    mutationFn: (patch: PersonalSettingsPatch) => updatePersonalSettings(patch),
    onSuccess: () => {
      setError(null)
      return queryClient.invalidateQueries({ queryKey: QUERY_KEY })
    },
    onError: (failure: Error) => setError(failure.message),
  })
  const locked = !profile.tools_available
  const roleplay = profile.mode === 'roleplay'

  return (
    <section className="miniapp-section-card selara-ai" aria-labelledby="personal-tools-title">
      <div className="miniapp-section-head">
        <h2 id="personal-tools-title">Инструменты</h2>
      </div>
      <p className="selara-ai__note">
        Все инструменты выключены, пока вы сами их не включите. Они делают ответ дольше и дороже, но запрос не тратит
        больше заранее зарезервированных AIL.
      </p>
      <label className="personal-switch">
        <input
          type="checkbox"
          checked={profile.tools_web_enabled}
          disabled={settings.isPending || (locked && !profile.tools_web_enabled)}
          onChange={(event) => settings.mutate({ tools_web_enabled: event.target.checked })}
        />
        <span>Веб-поиск: искать в интернете и читать страницы</span>
      </label>
      <label className="personal-switch">
        <input
          type="checkbox"
          checked={profile.tools_artifacts_enabled}
          disabled={settings.isPending || (locked && !profile.tools_artifacts_enabled)}
          onChange={(event) => settings.mutate({ tools_artifacts_enabled: event.target.checked })}
        />
        <span>Артефакты: таблицы, схемы и инфографика картинкой</span>
      </label>
      {locked && <p className="selara-ai__note">Нужна активная подписка Selara Personal.</p>}
      {roleplay && <p className="selara-ai__note">В ролевой игре инструменты недоступны.</p>}
      {error && (
        <p className="selara-ai__note" role="alert">
          {error}
        </p>
      )}
    </section>
  )
}
