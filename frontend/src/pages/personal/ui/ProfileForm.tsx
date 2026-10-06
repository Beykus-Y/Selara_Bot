import { useState } from 'react'

import { PersonalApiError, updatePersonalProfile } from '@/pages/personal/api/personal'
import type { PersonalOverview, PersonalProfile, PersonalProfileChanges } from '@/pages/personal/model/types'

type Props = {
  profile: PersonalProfile
  options: PersonalOverview['options']
  autoMemoryAvailable: boolean
  onSaved: (profile: PersonalProfile, notice: string) => void
}

const LENGTHS: { value: PersonalProfile['reply_length']; label: string }[] = [
  { value: 'short', label: 'Коротко' },
  { value: 'medium', label: 'Средне' },
  { value: 'long', label: 'Подробно' },
]

function diff(draft: PersonalProfile, saved: PersonalProfile): PersonalProfileChanges {
  const changes: PersonalProfileChanges = {}
  if (draft.display_name !== saved.display_name) changes.display_name = draft.display_name
  if (draft.character_preset !== saved.character_preset) changes.character_preset = draft.character_preset
  if ((draft.character_custom ?? '') !== (saved.character_custom ?? '')) changes.character_custom = draft.character_custom ?? ''
  if ((draft.address_form ?? '') !== (saved.address_form ?? '')) changes.address_form = draft.address_form ?? ''
  if (draft.formality !== saved.formality) changes.formality = draft.formality
  if (draft.reply_length !== saved.reply_length) changes.reply_length = draft.reply_length
  if (draft.emoji_enabled !== saved.emoji_enabled) changes.emoji_enabled = draft.emoji_enabled
  if (draft.mode !== saved.mode) changes.mode = draft.mode
  if (draft.memory_enabled !== saved.memory_enabled) changes.memory_enabled = draft.memory_enabled
  if (draft.auto_memory_enabled !== saved.auto_memory_enabled) changes.auto_memory_enabled = draft.auto_memory_enabled
  return changes
}

/** Re-mounted with a new ``key`` whenever the saved revision changes, so the draft always starts from the server copy. */
export function ProfileForm({ profile, options, autoMemoryAvailable, onSaved }: Props) {
  const [draft, setDraft] = useState<PersonalProfile>(profile)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const changes = diff(draft, profile)
  const dirty = Object.keys(changes).length > 0
  const isCustom = draft.character_preset === 'custom'

  const patch = (values: Partial<PersonalProfile>) => {
    setError(null)
    setDraft((current) => ({ ...current, ...values }))
  }

  const save = async () => {
    if (!dirty || saving) return
    setSaving(true)
    setError(null)
    try {
      onSaved(await updatePersonalProfile(profile.revision, changes), 'Настройки сохранены.')
    } catch (failure) {
      if (failure instanceof PersonalApiError && failure.code === 'revision_conflict' && failure.profile) {
        onSaved(failure.profile, 'Настройки уже изменились в другом месте, показываю актуальные.')
      } else {
        setError(failure instanceof Error ? failure.message : 'Не удалось сохранить настройки.')
      }
    } finally {
      setSaving(false)
    }
  }

  return (
    <form
      className="personal-form"
      onSubmit={(event) => {
        event.preventDefault()
        void save()
      }}
    >
      <label className="personal-field">
        <span>Имя собеседника</span>
        <input
          type="text"
          value={draft.display_name}
          maxLength={options.max_display_name}
          onChange={(event) => patch({ display_name: event.target.value })}
        />
      </label>

      <label className="personal-field">
        <span>Характер</span>
        <select value={draft.character_preset} onChange={(event) => patch({ character_preset: event.target.value })}>
          {options.presets.map((preset) => (
            <option key={preset.key} value={preset.key}>
              {preset.title}
            </option>
          ))}
        </select>
      </label>

      {isCustom && (
        <label className="personal-field">
          <span>Свой характер ({(draft.character_custom ?? '').length}/{options.max_custom_character})</span>
          <textarea
            rows={4}
            value={draft.character_custom ?? ''}
            maxLength={options.max_custom_character}
            onChange={(event) => patch({ character_custom: event.target.value })}
          />
        </label>
      )}

      <label className="personal-field">
        <span>Как обращаться к вам</span>
        <input
          type="text"
          value={draft.address_form ?? ''}
          maxLength={options.max_address}
          placeholder="по умолчанию"
          onChange={(event) => patch({ address_form: event.target.value })}
        />
      </label>

      <div className="personal-field">
        <span>Форма обращения</span>
        <div className="personal-segment" role="group" aria-label="Форма обращения">
          {(['ty', 'vy'] as const).map((value) => (
            <button
              key={value}
              type="button"
              className={draft.formality === value ? 'is-active' : ''}
              aria-pressed={draft.formality === value}
              onClick={() => patch({ formality: value })}
            >
              {value === 'ty' ? 'На «ты»' : 'На «вы»'}
            </button>
          ))}
        </div>
      </div>

      <div className="personal-field">
        <span>Длина ответов</span>
        <div className="personal-segment" role="group" aria-label="Длина ответов">
          {LENGTHS.map((item) => (
            <button
              key={item.value}
              type="button"
              className={draft.reply_length === item.value ? 'is-active' : ''}
              aria-pressed={draft.reply_length === item.value}
              onClick={() => patch({ reply_length: item.value })}
            >
              {item.label}
            </button>
          ))}
        </div>
      </div>

      <div className="personal-field">
        <span>Режим</span>
        <div className="personal-segment" role="group" aria-label="Режим">
          {(['assistant', 'roleplay'] as const).map((value) => (
            <button
              key={value}
              type="button"
              className={draft.mode === value ? 'is-active' : ''}
              aria-pressed={draft.mode === value}
              onClick={() => patch({ mode: value })}
            >
              {value === 'assistant' ? 'Помощник' : 'Ролевая игра'}
            </button>
          ))}
        </div>
        {draft.mode === 'roleplay' && (
          <small>У ролевой игры своя отдельная история; сцену и роли задайте в обычном сообщении боту.</small>
        )}
      </div>

      <label className="personal-check">
        <input type="checkbox" checked={draft.emoji_enabled} onChange={(event) => patch({ emoji_enabled: event.target.checked })} />
        <span>Использовать эмодзи</span>
      </label>
      <label className="personal-check">
        <input type="checkbox" checked={draft.memory_enabled} onChange={(event) => patch({ memory_enabled: event.target.checked })} />
        <span>Помнить факты обо мне</span>
      </label>
      <label className="personal-check">
        <input
          type="checkbox"
          checked={draft.auto_memory_enabled}
          disabled={!autoMemoryAvailable && !profile.auto_memory_enabled}
          onChange={(event) => patch({ auto_memory_enabled: event.target.checked })}
        />
        <span>Авто-запоминание (только Selara Personal)</span>
      </label>
      {!autoMemoryAvailable && !profile.auto_memory_enabled && (
        <small className="personal-hint">
          Нужна подписка Selara Personal, и функцию должен включить администратор бота.
        </small>
      )}

      {error && (
        <p className="personal-error" role="alert">
          {error}
        </p>
      )}
      <div className="personal-actions">
        <button type="submit" className="button button--primary" disabled={!dirty || saving}>
          {saving ? 'Сохраняю…' : 'Сохранить'}
        </button>
        {dirty && !saving && (
          <button type="button" className="button button--secondary" onClick={() => setDraft(profile)}>
            Отменить
          </button>
        )}
      </div>
    </form>
  )
}
