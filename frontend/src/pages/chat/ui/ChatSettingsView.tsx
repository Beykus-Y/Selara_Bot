import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'

import type { ChatSettingsData } from '@/pages/chat/model/types'
import { resolveAppPath } from '@/shared/config/app-base-path'
import { routes } from '@/shared/config/routes'

import './chat-page.css'

type TriggerDraft = {
  keyword: string
  match_type: string
  response_text: string
  media_file_id: string
  media_type: string
}

type ChatSettingsViewProps = {
  chatId: string
  data: ChatSettingsData
  feedbackMessage: string | null
  pendingKey: string | null
  isSaving: boolean
  onSave: (key: string, value: string) => Promise<void>
  onSaveAlias: (values: { alias_text: string; source_trigger: string }) => Promise<void>
  onDeleteAlias: (aliasText: string) => Promise<void>
  onSaveTrigger: (values: {
    trigger_id?: string
    keyword: string
    match_type: string
    response_text: string
    media_file_id: string
    media_type: string
  }) => Promise<void>
  onDeleteTrigger: (triggerId: string) => Promise<void>
}

function tabHref(chatId: string, tab: 'overview' | 'achievements' | 'settings') {
  return `/app/chat/${chatId}?tab=${tab}`
}

function buildInitialTriggerDrafts(data: ChatSettingsData) {
  return Object.fromEntries(
    data.triggers.map((trigger) => [
      trigger.id,
      {
        keyword: trigger.keyword,
        match_type: trigger.match_type,
        response_text: trigger.response_text,
        media_file_id: trigger.media_file_id,
        media_type: trigger.media_type,
      },
    ]),
  ) as Record<string, TriggerDraft>
}

export function ChatSettingsView({
  chatId,
  data,
  feedbackMessage,
  pendingKey,
  isSaving,
  onSave,
  onSaveAlias,
  onDeleteAlias,
  onSaveTrigger,
  onDeleteTrigger,
}: ChatSettingsViewProps) {
  const initialValues = useMemo(
    () =>
      Object.fromEntries(
        data.settings_sections.flatMap((section) =>
          section.items.map((item) => [item.key, item.current_value]),
        ),
      ) as Record<string, string>,
    [data.settings_sections],
  )
  const initialAliasDrafts = useMemo(
    () =>
      Object.fromEntries(
        data.aliases.map((alias) => [alias.alias, alias.source]),
      ) as Record<string, string>,
    [data.aliases],
  )
  const initialTriggerDrafts = useMemo(() => buildInitialTriggerDrafts(data), [data])
  const [draftValues, setDraftValues] = useState<Record<string, string>>(initialValues)
  const [aliasDrafts, setAliasDrafts] = useState<Record<string, string>>(initialAliasDrafts)
  const [newAlias, setNewAlias] = useState({ alias_text: '', source_trigger: '' })
  const [triggerDrafts, setTriggerDrafts] = useState<Record<string, TriggerDraft>>(initialTriggerDrafts)
  const [newTrigger, setNewTrigger] = useState<TriggerDraft>({
    keyword: '',
    match_type: 'contains',
    response_text: '',
    media_file_id: '',
    media_type: '',
  })

  useEffect(() => {
    setDraftValues(initialValues)
  }, [initialValues])

  useEffect(() => {
    setAliasDrafts(initialAliasDrafts)
  }, [initialAliasDrafts])

  useEffect(() => {
    setTriggerDrafts(initialTriggerDrafts)
  }, [initialTriggerDrafts])

  return (
    <div className="chat-page">
      <section className="chat-hero">
        <div>
          <span className="page-card__eyebrow">Группа</span>
          <h1>{data.chat_title}</h1>
          <p>Настройки, алиасы и смарт-триггеры управляются из браузера без командных полотен и ручного копирования значений.</p>
        </div>
        <div className="chat-hero__chips">
          <span className="chat-chip">ID чата {chatId}</span>
          <span className="chat-chip">Параметры и автоматизация</span>
          <span className="chat-chip">{data.can_manage_settings ? 'Можно редактировать' : 'Только просмотр'}</span>
        </div>
      </section>

      <section className="chat-tabs">
        <Link className="button" to={tabHref(chatId, 'overview')}>
          Обзор
        </Link>
        <Link className="button" to={tabHref(chatId, 'achievements')}>
          Достижения
        </Link>
        <Link className="button button--primary" to={tabHref(chatId, 'settings')}>
          Настройки
        </Link>
        <Link className="button" to={routes.economy(chatId)}>
          Экономика
        </Link>
        <Link className="button" to={routes.family(chatId)}>
          Моя семья
        </Link>
      </section>

      <section className={data.manage_settings_tone === 'ok' ? 'chat-banner chat-banner--ok' : 'chat-banner chat-banner--error'}>
        <div>
          <strong>{data.can_manage_settings ? 'Управление доступно' : 'Только чтение'}</strong>
          <p>{data.manage_settings_note}</p>
          {feedbackMessage ? <p className="chat-status">{feedbackMessage}</p> : null}
        </div>
        <a className="button" href={resolveAppPath(data.admin_docs_url)}>
          Открыть справку по настройкам
        </a>
      </section>

      {data.settings_sections.map((section) => (
        <section key={section.title} className="chat-panel chat-settings-section">
          <div className="chat-panel__head">
            <div>
              <h2>{section.title}</h2>
              <p>Значения взяты из текущей серверной конфигурации группы.</p>
            </div>
            <span className="chat-panel__tag">{section.items.length}</span>
          </div>

          {section.items.length > 0 ? (
            <div className="chat-settings-grid">
              {section.items.map((item) => (
                <article key={item.key} className="chat-setting-card">
                  <div className="chat-setting-head">
                    <div>
                      <h3>{item.title}</h3>
                      <p>{item.key}</p>
                    </div>
                    <span className="chat-panel__tag">{item.input_kind}</span>
                  </div>

                  <p>{item.description}</p>

                  <div className="chat-setting-values">
                    <div>
                      <span className="chat-metric__label">Текущее значение</span>
                      <strong>{item.current_value || '—'}</strong>
                    </div>
                    <div>
                      <span className="chat-metric__label">По умолчанию</span>
                      <strong>{item.default_value || '—'}</strong>
                    </div>
                  </div>

                  {data.can_manage_settings && item.editable ? (
                    <form
                      className="chat-setting-form"
                      onSubmit={(event) => {
                        event.preventDefault()
                        void onSave(item.key, draftValues[item.key] ?? item.current_value)
                      }}
                    >
                      {item.input_kind === 'textarea' ? (
                        <textarea
                          value={draftValues[item.key] ?? item.current_value}
                          onChange={(event) =>
                            setDraftValues((current) => ({ ...current, [item.key]: event.target.value }))
                          }
                          rows={4}
                        />
                      ) : item.input_kind === 'select' || item.input_kind === 'toggle' ? (
                        <select
                          value={draftValues[item.key] ?? item.current_value}
                          onChange={(event) =>
                            setDraftValues((current) => ({ ...current, [item.key]: event.target.value }))
                          }
                        >
                          {item.options.map((option) => (
                            <option key={`${item.key}-${option.value}`} value={option.value}>
                              {option.label}
                            </option>
                          ))}
                        </select>
                      ) : (
                        <input
                          type="text"
                          value={draftValues[item.key] ?? item.current_value}
                          onChange={(event) =>
                            setDraftValues((current) => ({ ...current, [item.key]: event.target.value }))
                          }
                        />
                      )}

                      <div className="chat-setting-actions">
                        <button className="button" type="submit" disabled={isSaving && pendingKey === item.key}>
                          {isSaving && pendingKey === item.key ? 'Сохраняю…' : 'Сохранить'}
                        </button>
                        <button
                          className="button button--secondary"
                          type="button"
                          disabled={isSaving}
                          onClick={() => {
                            void onSave(item.key, 'default')
                          }}
                        >
                          Сбросить
                        </button>
                      </div>
                    </form>
                  ) : item.options.length > 0 ? (
                    <div className="chat-setting-options">
                      {item.options.map((option) => (
                        <span
                          key={`${item.key}-${option.value}`}
                          className={option.selected ? 'chat-chip chat-chip--active' : 'chat-chip'}
                        >
                          {option.label}
                        </span>
                      ))}
                    </div>
                  ) : null}

                  <p className="chat-status">{item.hint}</p>

                  <a className="chat-setting-doc" href={`${resolveAppPath(data.admin_docs_url)}#${item.doc_anchor}`}>
                    Открыть описание параметра
                  </a>
                </article>
              ))}
            </div>
          ) : (
            <p className="chat-status">В этой секции пока нет доступных параметров.</p>
          )}
        </section>
      ))}

      <section className="chat-panel">
        <div className="chat-panel__head">
          <div>
            <h2>Алиасы команд</h2>
            <p>Свои короткие вызовы для стандартных текстовых команд группы.</p>
          </div>
          <span className="chat-panel__tag">{data.aliases.length}</span>
        </div>

        {data.can_manage_settings ? (
          <form
            className="chat-setting-card"
            onSubmit={(event) => {
              event.preventDefault()
              void (async () => {
                await onSaveAlias(newAlias)
                setNewAlias({ alias_text: '', source_trigger: '' })
              })()
            }}
          >
            <div className="chat-setting-head">
              <div>
                <h3>Новый алиас</h3>
                <p>Например: `топ` → `рейтинг`</p>
              </div>
            </div>
            <input
              type="text"
              value={newAlias.source_trigger}
              placeholder="Исходная команда или триггер"
              onChange={(event) => setNewAlias((current) => ({ ...current, source_trigger: event.target.value }))}
            />
            <input
              type="text"
              value={newAlias.alias_text}
              placeholder="Новый алиас"
              onChange={(event) => setNewAlias((current) => ({ ...current, alias_text: event.target.value }))}
            />
            <button
              className="button"
              type="submit"
              disabled={isSaving || !newAlias.alias_text.trim() || !newAlias.source_trigger.trim()}
            >
              Сохранить алиас
            </button>
          </form>
        ) : null}

        {data.aliases.length > 0 ? (
          <div className="chat-settings-grid">
            {data.aliases.map((alias) => (
              <article key={alias.id} className="chat-setting-card">
                <div className="chat-setting-head">
                  <div>
                    <h3>{alias.alias}</h3>
                    <p>/{alias.command}</p>
                  </div>
                </div>
                <p className="chat-status">Источник: {alias.source}</p>

                {data.can_manage_settings ? (
                  <form
                    className="chat-setting-form"
                    onSubmit={(event) => {
                      event.preventDefault()
                      void onSaveAlias({
                        alias_text: alias.alias,
                        source_trigger: aliasDrafts[alias.alias] ?? alias.source,
                      })
                    }}
                  >
                    <input
                      type="text"
                      value={aliasDrafts[alias.alias] ?? alias.source}
                      onChange={(event) =>
                        setAliasDrafts((current) => ({ ...current, [alias.alias]: event.target.value }))
                      }
                    />
                    <div className="chat-setting-actions">
                      <button className="button" type="submit" disabled={isSaving}>
                        Обновить
                      </button>
                      <button
                        className="button button--danger"
                        type="button"
                        disabled={isSaving}
                        onClick={() => {
                          void onDeleteAlias(alias.alias)
                        }}
                      >
                        Удалить
                      </button>
                    </div>
                  </form>
                ) : null}
              </article>
            ))}
          </div>
        ) : (
          <p className="chat-status">Кастомные алиасы пока не заданы.</p>
        )}
      </section>

      <section className="chat-panel">
        <div className="chat-panel__head">
          <div>
            <h2>Смарт-триггеры</h2>
            <p>Автоответы по точному совпадению, вхождению или началу сообщения.</p>
          </div>
          <span className="chat-panel__tag">{data.triggers.length}</span>
        </div>

        <article className="chat-setting-card">
          <div className="chat-setting-head">
            <div>
              <h3>Переменные шаблонов</h3>
              <p>Одинаково работают в Telegram и в браузере.</p>
            </div>
          </div>
          <div className="chat-setting-options">
            {data.trigger_template_quick_rows.map((item) => (
              <span key={item.token} className="chat-chip" title={item.description}>
                {item.token}
              </span>
            ))}
          </div>
          <div className="chat-trigger-example-list">
            {data.trigger_template_examples.map((example) => (
              <code key={example} className="chat-trigger-example">
                {example}
              </code>
            ))}
          </div>
          <a className="chat-setting-doc" href={resolveAppPath(data.trigger_template_docs_url)}>
            Открыть полный каталог переменных
          </a>
        </article>

        {data.can_manage_settings ? (
          <form
            className="chat-setting-card"
            onSubmit={(event) => {
              event.preventDefault()
              void (async () => {
                await onSaveTrigger(newTrigger)
                setNewTrigger({
                  keyword: '',
                  match_type: 'contains',
                  response_text: '',
                  media_file_id: '',
                  media_type: '',
                })
              })()
            }}
          >
            <div className="chat-setting-head">
              <div>
                <h3>Новый триггер</h3>
                <p>Можно сохранить текстовый ответ и необязательный media id.</p>
              </div>
            </div>
            <input
              type="text"
              placeholder="Ключевое слово или фраза"
              value={newTrigger.keyword}
              onChange={(event) => setNewTrigger((current) => ({ ...current, keyword: event.target.value }))}
            />
            <select
              value={newTrigger.match_type}
              onChange={(event) => setNewTrigger((current) => ({ ...current, match_type: event.target.value }))}
            >
              <option value="contains">Содержит фразу</option>
              <option value="exact">Точное совпадение</option>
              <option value="starts_with">Начинается с</option>
            </select>
            <textarea
              rows={4}
              placeholder="Ответ текстом. Поддерживает {user}, {chat}, {args}, {reply_user}..."
              value={newTrigger.response_text}
              onChange={(event) => setNewTrigger((current) => ({ ...current, response_text: event.target.value }))}
            />
            <input
              type="text"
              placeholder="ID медиафайла (необязательно)"
              value={newTrigger.media_file_id}
              onChange={(event) => setNewTrigger((current) => ({ ...current, media_file_id: event.target.value }))}
            />
            <input
              type="text"
              placeholder="Тип медиа: photo / sticker / video"
              value={newTrigger.media_type}
              onChange={(event) => setNewTrigger((current) => ({ ...current, media_type: event.target.value }))}
            />
            <button className="button" type="submit" disabled={isSaving || !newTrigger.keyword.trim()}>
              Сохранить триггер
            </button>
          </form>
        ) : null}

        {data.triggers.length > 0 ? (
          <div className="chat-settings-grid">
            {data.triggers.map((trigger) => {
              const draft = triggerDrafts[trigger.id] ?? {
                keyword: trigger.keyword,
                match_type: trigger.match_type,
                response_text: trigger.response_text,
                media_file_id: trigger.media_file_id,
                media_type: trigger.media_type,
              }

              return (
                <article key={trigger.id} className="chat-setting-card">
                  <div className="chat-setting-head">
                    <div>
                      <h3>{trigger.keyword}</h3>
                      <p>{trigger.match_type_label}</p>
                    </div>
                  </div>
                  <p className="chat-status">{trigger.preview}</p>

                  {data.can_manage_settings ? (
                    <form
                      className="chat-setting-form"
                      onSubmit={(event) => {
                        event.preventDefault()
                        void onSaveTrigger({
                          trigger_id: trigger.id,
                          ...draft,
                        })
                      }}
                    >
                      <input
                        type="text"
                        value={draft.keyword}
                        onChange={(event) =>
                          setTriggerDrafts((current) => ({
                            ...current,
                            [trigger.id]: { ...draft, keyword: event.target.value },
                          }))
                        }
                      />
                      <select
                        value={draft.match_type}
                        onChange={(event) =>
                          setTriggerDrafts((current) => ({
                            ...current,
                            [trigger.id]: { ...draft, match_type: event.target.value },
                          }))
                        }
                      >
                        <option value="contains">Содержит фразу</option>
                        <option value="exact">Точное совпадение</option>
                        <option value="starts_with">Начинается с</option>
                      </select>
                      <textarea
                        rows={4}
                        value={draft.response_text}
                        onChange={(event) =>
                          setTriggerDrafts((current) => ({
                            ...current,
                            [trigger.id]: { ...draft, response_text: event.target.value },
                          }))
                        }
                      />
                      <input
                        type="text"
                        placeholder="ID медиафайла"
                        value={draft.media_file_id}
                        onChange={(event) =>
                          setTriggerDrafts((current) => ({
                            ...current,
                            [trigger.id]: { ...draft, media_file_id: event.target.value },
                          }))
                        }
                      />
                      <input
                        type="text"
                        placeholder="Тип медиа"
                        value={draft.media_type}
                        onChange={(event) =>
                          setTriggerDrafts((current) => ({
                            ...current,
                            [trigger.id]: { ...draft, media_type: event.target.value },
                          }))
                        }
                      />
                      <div className="chat-setting-actions">
                        <button className="button" type="submit" disabled={isSaving || !draft.keyword.trim()}>
                          Обновить
                        </button>
                        <button
                          className="button button--danger"
                          type="button"
                          disabled={isSaving}
                          onClick={() => {
                            void onDeleteTrigger(trigger.id)
                          }}
                        >
                          Удалить
                        </button>
                      </div>
                    </form>
                  ) : (
                    <>
                      {trigger.response_text ? <p>{trigger.response_text}</p> : null}
                      {trigger.media_type || trigger.media_file_id ? (
                        <p className="chat-status">
                          Медиа: {trigger.media_type || 'не указан'} {trigger.media_file_id ? `• ${trigger.media_file_id}` : ''}
                        </p>
                      ) : null}
                    </>
                  )}
                </article>
              )
            })}
          </div>
        ) : (
          <p className="chat-status">Смарт-триггеры ещё не созданы.</p>
        )}
      </section>

      <section className="chat-panel">
        <div className="chat-panel__head">
          <div>
            <h2>Свежие действия</h2>
            <p>Последние изменения настроек и автоматизации в этой группе.</p>
          </div>
          <Link className="button" to={routes.audit(chatId)}>
            Открыть журнал
          </Link>
        </div>

        {data.audit_rows.length > 0 ? (
          <div className="chat-audit-list">
            {data.audit_rows.map((row, index) => (
              <div key={`${row.when}-${row.action}-${index}`} className="chat-audit-row">
                <div>
                  <strong>{row.action}</strong>
                  <p>{row.description}</p>
                </div>
                <span>{row.when}</span>
              </div>
            ))}
          </div>
        ) : (
          <p className="chat-status">Журнал действий пока пуст.</p>
        )}
      </section>
    </div>
  )
}
