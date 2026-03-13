import { useEffect, useMemo, useState, type MouseEvent as ReactMouseEvent } from 'react'

import type { ChatAliasModeSetting, ChatAliasSourceOption, ChatSettingsData } from '@/pages/chat/model/types'
import { resolveAppPath } from '@/shared/config/app-base-path'
import { ChatSectionNav } from '@/shared/ui/chat-section-nav/ChatSectionNav'

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

type DirtyEntry = {
  key: string
  title: string
  note: string
}

const FALLBACK_ALIAS_MODE_SETTING: ChatAliasModeSetting = {
  key: 'alias_mode',
  title: 'Режим алиасов',
  description: 'Параметр ещё не загружен полностью. Можно открыть страницу позже после повторного обновления.',
  hint: '',
  current_value: 'default',
  default_value: 'default',
  current_value_display: 'По умолчанию',
  default_value_display: 'По умолчанию',
  editable: false,
  input_kind: 'select',
  options: [{ value: 'default', label: 'По умолчанию', selected: true }],
  doc_href: '',
}

const EMPTY_SETTINGS_SECTIONS: NonNullable<ChatSettingsData['settings_sections']> = []
const EMPTY_ALIASES: NonNullable<ChatSettingsData['aliases']> = []
const EMPTY_TRIGGERS: NonNullable<ChatSettingsData['triggers']> = []
const EMPTY_ALIAS_SOURCE_OPTIONS: NonNullable<ChatSettingsData['alias_source_options']> = []
const EMPTY_TRIGGER_TEMPLATE_ROWS: NonNullable<ChatSettingsData['trigger_template_quick_rows']> = []
const EMPTY_TRIGGER_TEMPLATE_EXAMPLES: NonNullable<ChatSettingsData['trigger_template_examples']> = []
const EMPTY_SECTION_LINKS: NonNullable<ChatSettingsData['chat_section_links']> = []
const EMPTY_AUDIT_ROWS: NonNullable<ChatSettingsData['audit_rows']> = []

function buildInitialTriggerDrafts(data: ChatSettingsData) {
  const triggers = data.triggers ?? EMPTY_TRIGGERS

  return Object.fromEntries(
    triggers.map((trigger) => [
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

function hasTriggerDraftChanged(left: TriggerDraft, right: TriggerDraft) {
  return (
    left.keyword !== right.keyword ||
    left.match_type !== right.match_type ||
    left.response_text !== right.response_text ||
    left.media_file_id !== right.media_file_id ||
    left.media_type !== right.media_type
  )
}

function optionValues(options: ChatAliasSourceOption[], fallbackValue: string) {
  if (options.some((option) => option.value === fallbackValue)) {
    return options
  }

  return [{ value: fallbackValue, label: fallbackValue }, ...options]
}

function buildDocHref(baseHref: string, suffix: string) {
  if (!suffix) {
    return baseHref
  }

  return suffix.startsWith('#') ? `${baseHref}${suffix}` : `${baseHref}#${suffix}`
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
  const settingsSections = data.settings_sections ?? EMPTY_SETTINGS_SECTIONS
  const aliases = data.aliases ?? EMPTY_ALIASES
  const triggers = data.triggers ?? EMPTY_TRIGGERS
  const aliasSourceOptions = data.alias_source_options ?? EMPTY_ALIAS_SOURCE_OPTIONS
  const triggerTemplateQuickRows = data.trigger_template_quick_rows ?? EMPTY_TRIGGER_TEMPLATE_ROWS
  const triggerTemplateExamples = data.trigger_template_examples ?? EMPTY_TRIGGER_TEMPLATE_EXAMPLES
  const chatSectionLinks = data.chat_section_links ?? EMPTY_SECTION_LINKS
  const auditRows = data.audit_rows ?? EMPTY_AUDIT_ROWS
  const aliasModeSetting = data.alias_mode_setting ?? FALLBACK_ALIAS_MODE_SETTING
  const docsTemplateHref = data.trigger_template_docs_url ?? data.admin_docs_url
  const settingsOverview =
    data.settings_overview && data.settings_overview.length > 0
      ? data.settings_overview
      : [
          {
            title: 'Доступ',
            value: data.can_manage_settings ? 'Редактирование' : 'Только просмотр',
            meta: data.manage_settings_note,
          },
          {
            title: 'Алиасы',
            value: String(aliases.length),
            meta: 'кастомных сокращений',
          },
          {
            title: 'Триггеры',
            value: String(triggers.length),
            meta: 'смарт-правил ответа',
          },
          {
            title: 'Аудит',
            value: String(auditRows.length),
            meta: 'свежих записей журнала',
          },
        ]
  const docsBaseHref = resolveAppPath(data.admin_docs_url)
  const initialValues = useMemo(
    () =>
      Object.fromEntries(
        settingsSections.flatMap((section) =>
          section.items.map((item) => [item.key, item.current_value]),
        ),
      ) as Record<string, string>,
    [settingsSections],
  )
  const initialAliasDrafts = useMemo(
    () =>
      Object.fromEntries(
        aliases.map((alias) => [alias.alias, alias.source]),
      ) as Record<string, string>,
    [aliases],
  )
  const initialTriggerDrafts = useMemo(() => buildInitialTriggerDrafts(data), [data])
  const [draftValues, setDraftValues] = useState<Record<string, string>>(initialValues)
  const [aliasModeValue, setAliasModeValue] = useState(aliasModeSetting.current_value)
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
  const [docsGuardOpen, setDocsGuardOpen] = useState(false)
  const [pendingDocsHref, setPendingDocsHref] = useState<string | null>(null)
  const [isSavingDirtyChanges, setIsSavingDirtyChanges] = useState(false)

  useEffect(() => {
    setDraftValues(initialValues)
  }, [initialValues])

  useEffect(() => {
    setAliasModeValue(aliasModeSetting.current_value)
  }, [aliasModeSetting.current_value])

  useEffect(() => {
    setAliasDrafts(initialAliasDrafts)
  }, [initialAliasDrafts])

  useEffect(() => {
    setTriggerDrafts(initialTriggerDrafts)
  }, [initialTriggerDrafts])

  const dirtyEntries = useMemo(() => {
    const entries: DirtyEntry[] = []

    for (const section of settingsSections) {
      for (const item of section.items) {
        const draftValue = draftValues[item.key] ?? item.current_value
        if (draftValue !== item.current_value) {
          entries.push({
            key: `setting:${item.key}`,
            title: item.title,
            note: `новое значение: ${draftValue || 'пусто'}`,
          })
        }
      }
    }

    if (aliasModeValue !== aliasModeSetting.current_value) {
      entries.push({
        key: 'setting:alias_mode',
        title: aliasModeSetting.title,
        note: `новый режим: ${aliasModeValue}`,
      })
    }

    for (const alias of aliases) {
      const draftSource = aliasDrafts[alias.alias] ?? alias.source
      if (draftSource !== alias.source) {
        entries.push({
          key: `alias:${alias.alias}`,
          title: `Алиас ${alias.alias}`,
          note: `источник: ${draftSource}`,
        })
      }
    }

    if (newAlias.alias_text.trim() || newAlias.source_trigger.trim()) {
      entries.push({
        key: 'alias:new',
        title: 'Новый алиас',
        note: `${newAlias.alias_text || 'без имени'} -> ${newAlias.source_trigger || 'без источника'}`,
      })
    }

    for (const trigger of triggers) {
      const draft = triggerDrafts[trigger.id]
      if (draft && hasTriggerDraftChanged(draft, initialTriggerDrafts[trigger.id])) {
        entries.push({
          key: `trigger:${trigger.id}`,
          title: `Триггер ${trigger.keyword}`,
          note: 'есть несохранённые правки в форме триггера',
        })
      }
    }

    if (
      newTrigger.keyword.trim() ||
      newTrigger.response_text.trim() ||
      newTrigger.media_file_id.trim() ||
      newTrigger.media_type.trim()
    ) {
      entries.push({
        key: 'trigger:new',
        title: 'Новый триггер',
        note: newTrigger.keyword.trim() || 'черновик без ключевой фразы',
      })
    }

    return entries
  }, [
    aliasDrafts,
    aliasModeValue,
    aliasModeSetting.current_value,
    aliasModeSetting.title,
    aliases,
    draftValues,
    initialTriggerDrafts,
    newAlias,
    newTrigger,
    settingsSections,
    triggerDrafts,
    triggers,
  ])

  const hasUnsavedChanges = dirtyEntries.length > 0

  useEffect(() => {
    if (!hasUnsavedChanges) {
      return
    }

    const handleBeforeUnload = (event: BeforeUnloadEvent) => {
      event.preventDefault()
      event.returnValue = ''
    }

    window.addEventListener('beforeunload', handleBeforeUnload)

    return () => {
      window.removeEventListener('beforeunload', handleBeforeUnload)
    }
  }, [hasUnsavedChanges])

  const openDocsHref = (href: string) => {
    window.location.assign(resolveAppPath(href))
  }

  const handleDocsNavigation = (href: string, event?: ReactMouseEvent<HTMLAnchorElement>) => {
    if (!hasUnsavedChanges) {
      return
    }

    event?.preventDefault()
    setPendingDocsHref(href)
    setDocsGuardOpen(true)
  }

  const saveDirtyChanges = async () => {
    setIsSavingDirtyChanges(true)

    try {
      if (aliasModeValue !== aliasModeSetting.current_value) {
        await onSave(aliasModeSetting.key, aliasModeValue)
      }

      for (const section of settingsSections) {
        for (const item of section.items) {
          const draftValue = draftValues[item.key] ?? item.current_value
          if (draftValue !== item.current_value) {
            await onSave(item.key, draftValue)
          }
        }
      }

      for (const alias of aliases) {
        const draftSource = aliasDrafts[alias.alias] ?? alias.source
        if (draftSource !== alias.source) {
          await onSaveAlias({
            alias_text: alias.alias,
            source_trigger: draftSource,
          })
        }
      }

      if (newAlias.alias_text.trim() && newAlias.source_trigger.trim()) {
        await onSaveAlias(newAlias)
        setNewAlias({ alias_text: '', source_trigger: '' })
      }

      for (const trigger of triggers) {
        const draft = triggerDrafts[trigger.id]
        if (draft && hasTriggerDraftChanged(draft, initialTriggerDrafts[trigger.id])) {
          await onSaveTrigger({
            trigger_id: trigger.id,
            ...draft,
          })
        }
      }

      if (newTrigger.keyword.trim()) {
        await onSaveTrigger(newTrigger)
        setNewTrigger({
          keyword: '',
          match_type: 'contains',
          response_text: '',
          media_file_id: '',
          media_type: '',
        })
      }

      setDocsGuardOpen(false)

      if (pendingDocsHref) {
        openDocsHref(pendingDocsHref)
      }
    } finally {
      setIsSavingDirtyChanges(false)
    }
  }

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

      <ChatSectionNav links={chatSectionLinks} />

      <section className={data.manage_settings_tone === 'ok' ? 'chat-banner chat-banner--ok' : 'chat-banner chat-banner--error'}>
        <div>
          <strong>{data.can_manage_settings ? 'Управление доступно' : 'Только чтение'}</strong>
          <p>{data.manage_settings_note}</p>
          {feedbackMessage ? <p className="chat-status">{feedbackMessage}</p> : null}
        </div>
        <a
          className="button button--secondary"
          href={docsBaseHref}
          onClick={(event) => handleDocsNavigation(data.admin_docs_url, event)}
        >
          Открыть справку по настройкам
        </a>
      </section>

      <section className="chat-settings-summary">
        {settingsOverview.map((item) => (
          <article key={item.title} className="chat-summary-card">
            <span className="chat-metric__label">{item.title}</span>
            <strong>{item.value}</strong>
            <p>{item.meta}</p>
          </article>
        ))}
      </section>

      <section className="chat-panel chat-settings-section">
        <div className="chat-panel__head">
          <div>
            <h2>{aliasModeSetting.title}</h2>
            <p>{aliasModeSetting.description}</p>
          </div>
          <span className="chat-panel__tag">{aliasModeSetting.current_value_display}</span>
        </div>

        <article className="chat-setting-card">
          <div className="chat-setting-values">
            <div>
              <span className="chat-metric__label">Сейчас</span>
              <strong>{aliasModeSetting.current_value_display}</strong>
            </div>
            <div>
              <span className="chat-metric__label">По умолчанию</span>
              <strong>{aliasModeSetting.default_value_display}</strong>
            </div>
          </div>

          {data.can_manage_settings ? (
            <form
              className="chat-setting-form"
              onSubmit={(event) => {
                event.preventDefault()
                void onSave(aliasModeSetting.key, aliasModeValue)
              }}
            >
              <select value={aliasModeValue} onChange={(event) => setAliasModeValue(event.target.value)}>
                {aliasModeSetting.options.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
              <div className="chat-setting-actions">
                <button className="button" type="submit" disabled={isSaving && pendingKey === aliasModeSetting.key}>
                  {isSaving && pendingKey === aliasModeSetting.key ? 'Сохраняю…' : 'Сохранить режим'}
                </button>
                <button
                  className="button button--secondary"
                  type="button"
                  disabled={isSaving}
                  onClick={() => {
                    void onSave(aliasModeSetting.key, 'default')
                  }}
                >
                  Сбросить
                </button>
              </div>
            </form>
          ) : null}

          <a
            className="chat-setting-doc"
            href={buildDocHref(docsBaseHref, aliasModeSetting.doc_href)}
            onClick={(event) => handleDocsNavigation(buildDocHref(data.admin_docs_url, aliasModeSetting.doc_href), event)}
          >
            Открыть раздел документации
          </a>
        </article>
      </section>

      {settingsSections.map((section) => (
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
                      <strong>{item.current_value_display || '—'}</strong>
                    </div>
                    <div>
                      <span className="chat-metric__label">По умолчанию</span>
                      <strong>{item.default_value_display || '—'}</strong>
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

                  <a
                    className="chat-setting-doc"
                    href={`${docsBaseHref}#${item.doc_anchor}`}
                    onClick={(event) => handleDocsNavigation(`${data.admin_docs_url}#${item.doc_anchor}`, event)}
                  >
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
          <span className="chat-panel__tag">{aliases.length}</span>
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
                <p>Источник берётся только из встроенных текстовых триггеров.</p>
              </div>
            </div>
            <select
              value={newAlias.source_trigger}
              onChange={(event) => setNewAlias((current) => ({ ...current, source_trigger: event.target.value }))}
            >
              <option value="">Выберите встроенный триггер</option>
              {aliasSourceOptions.map((option) => (
                <option key={`new-alias-${option.value}`} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
            <input
              type="text"
              value={newAlias.alias_text}
              placeholder="Новый алиас, например: рейтинг"
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

        {aliases.length > 0 ? (
          <div className="chat-settings-grid">
            {aliases.map((alias) => (
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
                    <select
                      value={aliasDrafts[alias.alias] ?? alias.source}
                      onChange={(event) =>
                        setAliasDrafts((current) => ({ ...current, [alias.alias]: event.target.value }))
                      }
                    >
                      {optionValues(aliasSourceOptions, alias.source).map((option) => (
                        <option key={`${alias.id}-${option.value}`} value={option.value}>
                          {option.label}
                        </option>
                      ))}
                    </select>
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
          <span className="chat-panel__tag">{triggers.length}</span>
        </div>

        <article className="chat-setting-card">
          <div className="chat-setting-head">
            <div>
              <h3>Переменные шаблонов</h3>
              <p>Одинаково работают в Telegram и в браузере.</p>
            </div>
          </div>
          <div className="chat-setting-options">
            {triggerTemplateQuickRows.map((item) => (
              <span key={item.token} className="chat-chip" title={item.description}>
                {item.token}
              </span>
            ))}
          </div>
          <div className="chat-trigger-example-list">
            {triggerTemplateExamples.map((example) => (
              <code key={example} className="chat-trigger-example">
                {example}
              </code>
            ))}
          </div>
          <a
            className="chat-setting-doc"
            href={resolveAppPath(docsTemplateHref)}
            onClick={(event) => handleDocsNavigation(docsTemplateHref, event)}
          >
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

        {triggers.length > 0 ? (
          <div className="chat-settings-grid">
            {triggers.map((trigger) => {
              const draft = triggerDrafts[trigger.id] ?? initialTriggerDrafts[trigger.id]

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
                  ) : null}
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
            <h2>Журнал аудита</h2>
            <p>Свежие действия бота и админов в этой группе.</p>
          </div>
        </div>

        {auditRows.length > 0 ? (
          <div className="chat-audit-list">
            {auditRows.slice(0, 10).map((row, index) => (
              <div key={`${row.when}-${row.action}-${index}`} className="chat-audit-row">
                <div>
                  <strong>{row.action}</strong>
                  <p>{row.description}</p>
                  <p>Инициатор: {row.actor} • Цель: {row.target}</p>
                </div>
                <span>{row.when}</span>
              </div>
            ))}
          </div>
        ) : (
          <p className="chat-status">Логи пока пусты.</p>
        )}
      </section>

      {docsGuardOpen ? (
        <div className="docs-guard" role="dialog" aria-modal="true" aria-labelledby="docs-guard-title">
          <div className="docs-guard__card">
            <div className="chat-panel__head">
              <div>
                <h2 id="docs-guard-title">Есть несохранённые изменения</h2>
                <p>Перед переходом в документацию можно сохранить черновики или открыть справку без сохранения.</p>
              </div>
            </div>

            <div className="chat-banner chat-banner--error">
              <div>
                <strong>Список правок</strong>
                <p>Ниже собраны формы, которые отличаются от текущего сохранённого состояния.</p>
              </div>
            </div>

            <div className="docs-guard__list">
              {dirtyEntries.map((item) => (
                <div key={item.key} className="docs-guard__row">
                  <strong>{item.title}</strong>
                  <p>{item.note}</p>
                </div>
              ))}
            </div>

            <div className="docs-guard__actions">
              <button
                type="button"
                className="button button--primary"
                onClick={() => {
                  void saveDirtyChanges()
                }}
                disabled={isSavingDirtyChanges || isSaving}
              >
                {isSavingDirtyChanges ? 'Сохраняю…' : 'Сохранить и перейти'}
              </button>
              <button
                type="button"
                className="button button--secondary"
                onClick={() => {
                  setDocsGuardOpen(false)
                  if (pendingDocsHref) {
                    openDocsHref(pendingDocsHref)
                  }
                }}
                disabled={isSavingDirtyChanges}
              >
                Перейти без сохранения
              </button>
              <button
                type="button"
                className="button"
                onClick={() => {
                  setDocsGuardOpen(false)
                  setPendingDocsHref(null)
                }}
                disabled={isSavingDirtyChanges}
              >
                Остаться
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  )
}
