import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import {
  addPersonalMemory,
  deletePersonalMemory,
  getPersonalMemories,
  setPersonalMemoryPinned,
} from '@/pages/personal/api/personal'
import { memoryCounter, memoryExport } from '@/pages/personal/lib/view'
import type { PersonalMemory } from '@/pages/personal/model/types'

type Props = {
  memoryEnabled: boolean
  onChanged: () => void
}

export const MEMORY_KEY = ['miniapp-personal-memories'] as const

function errorText(error: unknown) {
  return error instanceof Error ? error.message : 'Не удалось выполнить действие.'
}

export function MemorySection({ memoryEnabled, onChanged }: Props) {
  const queryClient = useQueryClient()
  const [text, setText] = useState('')
  const [notice, setNotice] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [confirmingId, setConfirmingId] = useState<number | null>(null)

  const query = useQuery({
    queryKey: MEMORY_KEY,
    queryFn: ({ signal }) => getPersonalMemories(signal),
    staleTime: 10_000,
  })

  const refresh = async () => {
    await queryClient.invalidateQueries({ queryKey: MEMORY_KEY })
    onChanged()
  }

  const add = useMutation({
    mutationFn: (content: string) => addPersonalMemory(content),
    onSuccess: async () => {
      setText('')
      setError(null)
      setNotice('Запомнила.')
      await refresh()
    },
    onError: (failure) => {
      setNotice(null)
      setError(errorText(failure))
      // A duplicate or a full memory can mean another device changed the list.
      void refresh()
    },
  })

  const pin = useMutation({
    mutationFn: ({ id, pinned }: { id: number; pinned: boolean }) => setPersonalMemoryPinned(id, pinned),
    onSuccess: refresh,
    onError: (failure) => {
      setError(errorText(failure))
      void refresh()
    },
  })

  const remove = useMutation({
    mutationFn: (id: number) => deletePersonalMemory(id),
    onSuccess: async () => {
      setConfirmingId(null)
      setNotice('Забыла.')
      setError(null)
      await refresh()
    },
    onError: (failure) => {
      setConfirmingId(null)
      setError(errorText(failure))
      void refresh()
    },
  })

  const data = query.data
  const items: PersonalMemory[] = data?.items ?? []
  const full = data !== undefined && data.limit !== null && data.count >= data.limit
  const canSubmit = text.trim().length > 0 && !add.isPending && memoryEnabled && !full

  const copyAll = async () => {
    try {
      await navigator.clipboard.writeText(memoryExport(items))
      setError(null)
      setNotice('Список скопирован.')
    } catch {
      setNotice(null)
      setError('Не удалось скопировать. Выделите текст вручную.')
    }
  }

  return (
    <section className="personal-memory" aria-labelledby="personal-memory-title">
      <h2 className="sec" id="personal-memory-title">
        Память {data ? <span className="personal-count">{memoryCounter(data.count, data.limit)}</span> : null}
      </h2>
      <div className="card personal-card">
        {!memoryEnabled && (
          <p className="personal-hint">
            Память выключена: факты хранятся, но в разговоре не используются и новые не сохраняются.
          </p>
        )}

        <form
          className="personal-add"
          onSubmit={(event) => {
            event.preventDefault()
            if (canSubmit) add.mutate(text)
          }}
        >
          <label className="personal-field">
            <span>Новый факт ({text.length}/{data?.max_length ?? 300})</span>
            <textarea
              rows={2}
              value={text}
              maxLength={data?.max_length ?? 300}
              placeholder="Например: я не ем орехи"
              disabled={!memoryEnabled}
              onChange={(event) => {
                setText(event.target.value)
                setError(null)
                setNotice(null)
              }}
            />
          </label>
          <button type="submit" className="button button--primary" disabled={!canSubmit}>
            {add.isPending ? 'Сохраняю…' : 'Запомнить'}
          </button>
          {full && <small className="personal-hint">Лимит памяти достигнут: удалите лишнее, я ничего не стираю сама.</small>}
        </form>

        {error && (
          <p className="personal-error" role="alert">
            {error}
          </p>
        )}
        {notice && !error && (
          <p className="personal-notice" role="status">
            {notice}
          </p>
        )}

        {query.isPending && <p className="personal-hint">Загружаю память…</p>}
        {query.isError && (
          <p className="personal-error" role="alert">
            {errorText(query.error)}{' '}
            <button type="button" className="personal-link" onClick={() => void query.refetch()}>
              Повторить
            </button>
          </p>
        )}
        {data && items.length === 0 && (
          <p className="personal-hint">Пока пусто. Напишите факт выше или скажите боту «запомни, что …».</p>
        )}

        {items.length > 0 && (
          <ul className="personal-facts">
            {items.map((item) => (
              <li key={item.id} className={item.pinned ? 'is-pinned' : ''}>
                <p>
                  {item.pinned && <span aria-label="Закреплено">📌 </span>}
                  {item.content}
                  {item.source === 'extracted' && <em className="personal-source"> (авто)</em>}
                </p>
                <div className="personal-fact-actions">
                  <button
                    type="button"
                    className="personal-link"
                    disabled={pin.isPending}
                    onClick={() => pin.mutate({ id: item.id, pinned: !item.pinned })}
                  >
                    {item.pinned ? 'Открепить' : 'Закрепить'}
                  </button>
                  {confirmingId === item.id ? (
                    <>
                      <button
                        type="button"
                        className="personal-link personal-link--danger"
                        disabled={remove.isPending}
                        onClick={() => remove.mutate(item.id)}
                      >
                        Да, забыть
                      </button>
                      <button type="button" className="personal-link" onClick={() => setConfirmingId(null)}>
                        Нет
                      </button>
                    </>
                  ) : (
                    <button type="button" className="personal-link personal-link--danger" onClick={() => setConfirmingId(item.id)}>
                      Удалить
                    </button>
                  )}
                </div>
              </li>
            ))}
          </ul>
        )}

        {items.length > 0 && (
          <div className="personal-actions">
            <button type="button" className="button button--secondary" onClick={() => void copyAll()}>
              Скопировать список
            </button>
          </div>
        )}
        <p className="personal-hint">
          Полностью стереть профиль, историю и память можно командой /forget_all в личке с ботом.
        </p>
      </div>
    </section>
  )
}
