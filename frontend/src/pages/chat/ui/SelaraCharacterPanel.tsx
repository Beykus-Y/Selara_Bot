import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { getSelaraChatSettings, updateSelaraChatSettings } from '@/pages/chat/api/selara-character'
import type { SelaraAction, SelaraChatSettings } from '@/pages/chat/model/selara-character'

import './selara-ai-panel.css'

type ToggleKey = 'member_mode' | 'history' | 'actions'

const TOGGLES: Array<{ key: ToggleKey; label: string; hint: string }> = [
  { key: 'member_mode', label: 'Отвечать участникам по кличке', hint: 'Без этого Selara не реагирует на обращения.' },
  { key: 'history', label: 'Читать недавние сообщения чата', hint: 'До суток, только когда к ней обращаются.' },
  { key: 'actions', label: 'Действия как у участника', hint: 'Может сама обнять, дать пять и т.п. (без 18+).' },
]

function PanelBody({ chatId, data }: { chatId: string; data: SelaraChatSettings }) {
  const queryClient = useQueryClient()
  const [newName, setNewName] = useState('')
  const [custom, setCustom] = useState(data.character.custom ?? '')
  const [feedback, setFeedback] = useState<string | null>(null)
  const disabled = !data.can_manage

  const mutation = useMutation({
    mutationFn: (vars: { action: SelaraAction; value?: string }) =>
      updateSelaraChatSettings(chatId, vars.action, vars.value ?? ''),
    onSuccess: ({ message, ...next }) => {
      queryClient.setQueryData(['miniapp-chat-selara', chatId], next)
      setFeedback(message)
    },
    onError: (error: Error) => setFeedback(error.message),
  })

  const run = (action: SelaraAction, value?: string) => mutation.mutate({ action, value })
  const preset = data.character.preset

  return (
    <>
      <p className="selara-ai__note">
        Клички ({data.names.length}/{data.name_limit}){data.paid ? '' : ': несколько кличек открывает Selara AI'}.
        Лимит: {data.limits.daily} обращений в сутки на чат, {data.limits.per_actor} на участника.
      </p>
      <ul className="selara-char__names">
        {data.names.length === 0 && <li className="selara-ai__note">Кличек пока нет.</li>}
        {data.names.map((name) => (
          <li key={name.norm}>
            <strong>{name.display}</strong>
            {name.is_primary && <small> основная</small>}
            {!name.active && <small> не работает без Selara AI</small>}
            {!disabled && (
              <span className="selara-char__row-actions">
                {!name.is_primary && (
                  <button className="button button--secondary" type="button" disabled={mutation.isPending}
                    onClick={() => run('set_primary', name.norm)}>Основная</button>
                )}
                <button className="button button--secondary" type="button" disabled={mutation.isPending}
                  onClick={() => run('remove_name', name.norm)}>Убрать</button>
              </span>
            )}
          </li>
        ))}
      </ul>
      {!disabled && (
        <form className="selara-char__form" onSubmit={(event) => {
          event.preventDefault()
          if (newName.trim()) {
            run('add_name', newName.trim())
            setNewName('')
          }
        }}>
          <input type="text" value={newName} maxLength={24} placeholder="Новая кличка, например Селя"
            onChange={(event) => setNewName(event.target.value)} aria-label="Новая кличка" />
          <button className="button button--primary" type="submit" disabled={mutation.isPending || !newName.trim()}>
            Добавить
          </button>
        </form>
      )}

      <h3 className="selara-char__heading">Характер</h3>
      <div className="selara-char__presets" role="group" aria-label="Пресеты характера">
        {data.character.presets.map((item) => (
          <button key={item.key} type="button" disabled={disabled || mutation.isPending}
            className={`button ${preset === item.key ? 'button--primary' : 'button--secondary'}`}
            aria-pressed={preset === item.key} onClick={() => run('set_preset', item.key)}>
            {item.title}
          </button>
        ))}
      </div>
      <form className="selara-char__form" onSubmit={(event) => {
        event.preventDefault()
        run('set_custom', custom)
      }}>
        <textarea value={custom} maxLength={500} rows={3} disabled={disabled}
          placeholder="Свой характер, до 500 символов" aria-label="Свой характер"
          onChange={(event) => setCustom(event.target.value)} />
        {!disabled && (
          <button className="button button--secondary" type="submit" disabled={mutation.isPending || !custom.trim()}>
            Сохранить свой
          </button>
        )}
      </form>

      <h3 className="selara-char__heading">Поведение</h3>
      <ul className="selara-char__toggles">
        {TOGGLES.map((toggle) => (
          <li key={toggle.key}>
            <label>
              <input type="checkbox" checked={data[toggle.key]} disabled={disabled || mutation.isPending}
                onChange={(event) => run(toggle.key, event.target.checked ? 'true' : 'false')} />
              <span>{toggle.label}</span>
            </label>
            <small>{toggle.hint}</small>
          </li>
        ))}
      </ul>
      {!disabled && (
        <button className="button button--secondary" type="button" disabled={mutation.isPending}
          onClick={() => run('reset_history')}>
          Забыть разговор с участниками
        </button>
      )}
      {disabled && <p className="selara-ai__note">Менять настройки могут админы с правом настройки чата.</p>}
      {feedback && <p className="selara-ai__note" role="status">{feedback}</p>}
    </>
  )
}

export function SelaraCharacterPanel({ chatId }: { chatId: string }) {
  const query = useQuery({
    queryKey: ['miniapp-chat-selara', chatId],
    queryFn: ({ signal }) => getSelaraChatSettings(chatId, { signal }),
    staleTime: 15_000,
  })

  return (
    <section className="miniapp-section-card selara-char" aria-labelledby="selara-char-title" aria-busy={query.isFetching}>
      <div className="miniapp-section-head">
        <h2 id="selara-char-title">Selara в чате</h2>
      </div>
      {query.isPending ? (
        <div className="selara-ai__skeleton" aria-label="Загрузка настроек Selara"><i /><i /><i /></div>
      ) : query.isError && !query.data ? (
        <div className="selara-ai__error" role="alert">
          <span>{query.error.message}</span>
          <button className="button button--secondary" type="button" onClick={() => void query.refetch()}>Повторить</button>
        </div>
      ) : query.data ? (
        <PanelBody chatId={chatId} data={query.data} />
      ) : null}
    </section>
  )
}
