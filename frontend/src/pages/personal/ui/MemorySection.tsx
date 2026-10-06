import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import type { FormEvent } from 'react'

import {
  addPersonalMemory,
  deletePersonalMemory,
  pinPersonalMemory,
  updatePersonalSettings,
} from '@/pages/personal/api/personal-api'
import type { PersonalOverview, PersonalSettingsPatch } from '@/pages/personal/model/types'

const MAX_FACT_LENGTH = 300
const QUERY_KEY = ['miniapp-personal']

export function MemorySection({ data }: { data: PersonalOverview }) {
  const queryClient = useQueryClient()
  const { memory, profile } = data
  const [draft, setDraft] = useState('')
  const [notice, setNotice] = useState<{ tone: 'ok' | 'error'; text: string } | null>(null)

  const refresh = () => queryClient.invalidateQueries({ queryKey: QUERY_KEY })
  const fail = (error: Error) => setNotice({ tone: 'error', text: error.message })

  const add = useMutation({
    mutationFn: (content: string) => addPersonalMemory(content),
    onSuccess: (result) => {
      setDraft('')
      setNotice({ tone: 'ok', text: result.status === 'duplicate' ? 'Это я уже помню.' : 'Запомнила.' })
      return refresh()
    },
    onError: fail,
  })
  const remove = useMutation({
    mutationFn: (id: number) => deletePersonalMemory(id),
    onSuccess: () => {
      setNotice(null)
      return refresh()
    },
    onError: fail,
  })
  const pin = useMutation({
    mutationFn: ({ id, pinned }: { id: number; pinned: boolean }) => pinPersonalMemory(id, pinned),
    onSuccess: () => {
      setNotice(null)
      return refresh()
    },
    onError: fail,
  })
  const settings = useMutation({
    mutationFn: (patch: PersonalSettingsPatch) => updatePersonalSettings(patch),
    onSuccess: () => {
      setNotice(null)
      return refresh()
    },
    onError: fail,
  })

  const busy = add.isPending || remove.isPending || pin.isPending || settings.isPending
  const limitText = memory.limit === null ? `${memory.count}` : `${memory.count} из ${memory.limit}`
  const full = memory.limit !== null && memory.count >= memory.limit
  const canAdd = profile.memory_enabled && memory.limit !== null && !full

  const submit = (event: FormEvent) => {
    event.preventDefault()
    const text = draft.trim()
    if (!text || busy || !canAdd) return
    add.mutate(text)
  }

  const copyAll = async () => {
    const text = memory.items.map((item, index) => `${index + 1}. ${item.content}`).join('\n')
    try {
      await navigator.clipboard.writeText(text)
      setNotice({ tone: 'ok', text: 'Список скопирован.' })
    } catch {
      setNotice({ tone: 'error', text: 'Не удалось скопировать. Выделите текст вручную.' })
    }
  }

  return (
    <section className="miniapp-section-card personal-memory" aria-labelledby="personal-memory-title" aria-busy={busy}>
      <div className="miniapp-section-head">
        <h2 id="personal-memory-title">Память ({limitText})</h2>
        {memory.items.length > 0 && (
          <button className="button button--secondary" type="button" onClick={() => void copyAll()}>
            Копировать список
          </button>
        )}
      </div>

      <label className="personal-switch">
        <input
          type="checkbox"
          checked={profile.memory_enabled}
          disabled={busy}
          onChange={(event) => settings.mutate({ memory_enabled: event.target.checked })}
        />
        <span>Использовать память в разговоре</span>
      </label>
      <label className="personal-switch">
        <input
          type="checkbox"
          checked={profile.auto_memory_enabled}
          disabled={busy || !profile.memory_enabled}
          onChange={(event) => settings.mutate({ auto_memory_enabled: event.target.checked })}
        />
        <span>Предлагать запоминать факты автоматически</span>
      </label>
      {!profile.auto_memory_available && (
        <p className="selara-ai__note">
          Автоматическое запоминание работает только с активной Selara Personal и пока включено администратором бота.
        </p>
      )}

      <form className="personal-add" onSubmit={submit}>
        <label htmlFor="personal-fact">Новый факт о себе</label>
        <div className="personal-add__row">
          <input
            id="personal-fact"
            type="text"
            value={draft}
            maxLength={MAX_FACT_LENGTH}
            placeholder="Например: я не ем орехи"
            disabled={!canAdd}
            onChange={(event) => setDraft(event.target.value)}
          />
          <button className="button" type="submit" disabled={!canAdd || busy || !draft.trim()}>
            Запомнить
          </button>
        </div>
        {!profile.memory_enabled && <small className="selara-ai__note">Память выключена: включите её выше, чтобы добавлять факты.</small>}
        {profile.memory_enabled && full && (
          <small className="selara-ai__note">Лимит исчерпан. Удалите лишний факт, и можно будет добавить новый.</small>
        )}
        {memory.limit === null && (
          <small className="selara-ai__note">Лимит сейчас не удалось проверить, добавление временно недоступно.</small>
        )}
      </form>

      {notice && (
        <p className={`personal-notice is-${notice.tone}`} role={notice.tone === 'error' ? 'alert' : 'status'}>
          {notice.text}
        </p>
      )}

      {memory.items.length === 0 ? (
        <p className="selara-ai__note">Пока пусто. Напишите факт выше или скажите боту в личке: «запомни, что я веган».</p>
      ) : (
        <ul className="personal-facts">
          {memory.items.map((item) => (
            <li key={item.id} className={item.pinned ? 'is-pinned' : undefined}>
              <span className="personal-facts__text">
                {item.pinned ? '📌 ' : ''}
                {item.content}
                {item.source !== 'explicit' && <em> (авто)</em>}
              </span>
              <span className="personal-facts__actions">
                <button
                  className="button button--secondary"
                  type="button"
                  disabled={busy}
                  aria-label={`${item.pinned ? 'Открепить' : 'Закрепить'}: ${item.content}`}
                  onClick={() => pin.mutate({ id: item.id, pinned: !item.pinned })}
                >
                  {item.pinned ? 'Открепить' : 'Закрепить'}
                </button>
                <button
                  className="button button--danger"
                  type="button"
                  disabled={busy}
                  aria-label={`Забыть: ${item.content}`}
                  onClick={() => remove.mutate(item.id)}
                >
                  Забыть
                </button>
              </span>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
