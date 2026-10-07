import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import type { FormEvent } from 'react'
import { getAdminModels, getAdminProfiles, saveAdminModel, saveAdminProfile } from '../api/admin-models'
import type { Capabilities, CatalogModel, ModelProfile } from '../api/admin-models'
import { SectionError, SectionRetry, SectionSkeleton } from './AdminAiParts'
import './admin-models.css'

const capabilityLabels: Record<keyof Capabilities, string> = {
  supports_tools: 'Tools', supports_structured_output: 'Structured output', supports_vision: 'Vision',
}
const capabilityKeys = Object.keys(capabilityLabels) as Array<keyof Capabilities>
const multiplierNote = 'AIL multiplier — сколько AI Limits списывает один запрос Personal AI через этот профиль. Действует только в режиме AI Limits (см. «Система лимитов»); в режиме запросов лимиты 5/150 не зависят от него. Для AIL multiplier задаётся с точностью до 0.01.'

function ModelInfo({ model }: { model: CatalogModel }) {
  return <>
    <p className="admin-mono">{model.model_id}</p>
    <p>Input: {model.prompt_price_usd_per_million === null ? 'цена неизвестна' : `$${model.prompt_price_usd_per_million}`} / 1M · Output: {model.completion_price_usd_per_million === null ? 'цена неизвестна' : `$${model.completion_price_usd_per_million}`} / 1M</p>
    <p>{capabilityKeys.map((key) => `${model.capabilities[key] ? '✓' : '✕'} ${capabilityLabels[key]}`).join(' · ')}</p>
    {!model.capabilities.supports_tools && <p className="admin-warning">Эта модель не поддерживает tools. Для операций с tools применяется fallback.</p>}
    {(model.prompt_price_usd_per_million === null || model.completion_price_usd_per_million === null) && <p className="admin-warning">Стоимость части вызовов будет неизвестна.</p>}
  </>
}

function ProfileEditor({ profile, models, onSaved, onCancel }: {
  profile: ModelProfile; models: CatalogModel[]; onSaved: () => Promise<void>; onCancel: () => void
}) {
  const [name, setName] = useState(profile.display_name)
  const [key, setKey] = useState(profile.model_key ?? '')
  const [multiplier, setMultiplier] = useState(profile.ail_multiplier)
  const [enabled, setEnabled] = useState(profile.enabled)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  async function submit(event: FormEvent) {
    event.preventDefault()
    setError('')
    setBusy(true)
    try {
      await saveAdminProfile(profile.profile_key, {
        display_name: name, model_key: key || null, ail_multiplier: multiplier, enabled, revision: profile.revision,
      })
      await onSaved()
    } catch (failure) { setError(failure instanceof Error ? failure.message : 'Не удалось сохранить.') }
    finally { setBusy(false) }
  }
  return <form className="admin-model-form" onSubmit={(event) => void submit(event)}>
    <label>Название профиля<input required maxLength={255} value={name} onChange={(event) => setName(event.target.value)} /></label>
    <label>Модель<select aria-label="Модель" value={key} onChange={(event) => setKey(event.target.value)}>
      <option value="">Legacy fallback</option>
      {models.filter((model) => model.enabled || model.key === profile.model_key).map((model) => (
        <option key={model.key} value={model.key} disabled={!model.enabled}>{model.display_name}{!model.enabled ? ' (выключена)' : ''}</option>
      ))}
    </select></label>
    <label>AIL multiplier<input required inputMode="decimal" value={multiplier} onChange={(event) => setMultiplier(event.target.value)} /></label>
    <label className="admin-model-check"><input type="checkbox" checked={enabled} onChange={(event) => setEnabled(event.target.checked)} />Профиль включён</label>
    <p className="admin-footnote">{multiplierNote}</p>
    {error && <p role="alert" className="admin-warning">{error}</p>}
    <div className="admin-model-actions"><button disabled={busy} type="submit">{busy ? 'Сохраняю…' : 'Сохранить профиль'}</button><button disabled={busy} type="button" onClick={onCancel}>Отмена</button></div>
  </form>
}

function ModelEditor({ model, onSaved, onCancel }: {
  model: CatalogModel | null; onSaved: () => Promise<void>; onCancel: () => void
}) {
  const [key, setKey] = useState(model?.key ?? '')
  const [name, setName] = useState(model?.display_name ?? '')
  const [id, setId] = useState(model?.model_id ?? '')
  const [input, setInput] = useState(model?.prompt_price_usd_per_million ?? '')
  const [output, setOutput] = useState(model?.completion_price_usd_per_million ?? '')
  const [aliases, setAliases] = useState(model?.aliases.join('\n') ?? '')
  const [enabled, setEnabled] = useState(model?.enabled ?? true)
  const [capabilities, setCapabilities] = useState<Capabilities>(model?.capabilities ?? {
    supports_tools: false, supports_structured_output: false, supports_vision: false,
  })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  async function submit(event: FormEvent) {
    event.preventDefault()
    const disablingAssigned = !!model?.enabled && !enabled && model.used_by_profiles.length > 0
    if (disablingAssigned && !window.confirm(`Модель используется профилями: ${model.used_by_profiles.join(', ')}. Выключить и использовать fallback?`)) return
    setError('')
    setBusy(true)
    try {
      await saveAdminModel(model?.key ?? null, {
        ...(model ? { revision: model.revision, confirm_disable: disablingAssigned } : { key }),
        display_name: name, model_id: id, enabled, capabilities,
        prompt_price_usd_per_million: input.trim() === '' ? null : input,
        completion_price_usd_per_million: output.trim() === '' ? null : output,
        aliases: aliases.split('\n').map((alias) => alias.trim()).filter(Boolean),
      })
      await onSaved()
    } catch (failure) { setError(failure instanceof Error ? failure.message : 'Не удалось сохранить.') }
    finally { setBusy(false) }
  }
  return <form className="admin-model-form" onSubmit={(event) => void submit(event)}>
    <label>Название модели<input required maxLength={255} value={name} onChange={(event) => setName(event.target.value)} /></label>
    <label>Catalog key<input required maxLength={64} pattern="[a-z][a-z0-9_]*" readOnly={!!model} value={key} onChange={(event) => setKey(event.target.value)} /></label>
    <label>Provider model ID<input required maxLength={255} value={id} onChange={(event) => setId(event.target.value)} /></label>
    <label>Input USD / 1M tokens<input inputMode="decimal" placeholder="Неизвестна" value={input} onChange={(event) => setInput(event.target.value)} /></label>
    <label>Output USD / 1M tokens<input inputMode="decimal" placeholder="Неизвестна" value={output} onChange={(event) => setOutput(event.target.value)} /></label>
    <p className="admin-footnote">Пустая цена — неизвестна; 0 — бесплатная. Новые цены применяются только к будущим вызовам.</p>
    <label>Aliases — по одному на строку<textarea rows={3} value={aliases} onChange={(event) => setAliases(event.target.value)} /></label>
    {capabilityKeys.map((flag) => <label className="admin-model-check" key={flag}><input type="checkbox" checked={capabilities[flag]} onChange={(event) => setCapabilities({ ...capabilities, [flag]: event.target.checked })} />{capabilityLabels[flag]}</label>)}
    <label className="admin-model-check"><input type="checkbox" checked={enabled} onChange={(event) => setEnabled(event.target.checked)} />Модель включена</label>
    {error && <p role="alert" className="admin-warning">{error}</p>}
    <div className="admin-model-actions"><button disabled={busy} type="submit">{busy ? 'Сохраняю…' : 'Сохранить модель'}</button><button disabled={busy} type="button" onClick={onCancel}>Отмена</button></div>
  </form>
}

export function AdminModelsSection() {
  const client = useQueryClient()
  const catalog = useQuery({ queryKey: ['admin-models'], queryFn: getAdminModels, staleTime: 15_000 })
  const profiles = useQuery({ queryKey: ['admin-model-profiles'], queryFn: getAdminProfiles, staleTime: 15_000 })
  const [editor, setEditor] = useState<{ model: CatalogModel | null } | null>(null)
  const [profileEditor, setProfileEditor] = useState<ModelProfile | null>(null)
  const [saved, setSaved] = useState(false)
  async function reload() {
    await Promise.all([
      client.invalidateQueries({ queryKey: ['admin-models'] }),
      client.invalidateQueries({ queryKey: ['admin-model-profiles'] }),
    ])
  }
  function refresh() {
    setEditor(null)
    setProfileEditor(null)
    setSaved(false)
    void reload()
  }
  async function onSaved() {
    setEditor(null)
    setProfileEditor(null)
    setSaved(true)
    await reload()
  }
  return <section className="admin-section admin-models" aria-labelledby="admin-models-title">
    <div className="admin-section__title-row"><h2 id="admin-models-title">Модели AI</h2><SectionRetry busy={catalog.isFetching || profiles.isFetching} onRetry={refresh} /></div>
    <p className="admin-footnote">{multiplierNote}</p>
    {saved && <p role="status">Сохранено. Применяется без рестарта в течение 15 секунд.</p>}
    {(catalog.isPending || profiles.isPending) ? <SectionSkeleton rows={3} /> : catalog.isError || profiles.isError ? (
      <SectionError message={catalog.error?.message ?? profiles.error?.message ?? 'Не удалось загрузить конфигурацию.'} busy={catalog.isFetching || profiles.isFetching} onRetry={refresh} />
    ) : <>
      <h3>Профили</h3>
      <p className="admin-footnote">{profiles.data?.fallback_note}</p>
      {profiles.data?.items.map((profile) => <article className="admin-model-card" key={profile.profile_key}>
        <strong>{profile.display_name}</strong><p className="admin-mono">{profile.profile_key}</p>
        <p>AIL: ×{profile.ail_multiplier} · {profile.enabled ? 'Включён' : 'Выключен'}</p>
        <p className="admin-mono">Effective model: {profile.effective.model_id}</p>
        {profile.effective.is_fallback && <p>Используется legacy fallback (LLM_MODEL; для итогов LLM_SUMMARY_MODEL).</p>}
        {profile.assigned_model && !profile.assigned_model.enabled && <p className="admin-warning">Назначенная модель выключена. Используется fallback.</p>}
        {profile.assigned_model && <ModelInfo model={profile.assigned_model} />}
        {profileEditor?.profile_key === profile.profile_key ? <ProfileEditor profile={profileEditor} models={catalog.data?.items ?? []} onSaved={onSaved} onCancel={() => setProfileEditor(null)} /> : <button type="button" onClick={() => { setProfileEditor(profile); setSaved(false) }}>Изменить профиль {profile.display_name}</button>}
      </article>)}
      <h3>Каталог моделей</h3>
      <button type="button" onClick={() => { setEditor({ model: null }); setSaved(false) }}>Добавить модель</button>
      {editor?.model === null && <ModelEditor model={null} onSaved={onSaved} onCancel={() => setEditor(null)} />}
      {catalog.data?.items.length === 0 && <p className="admin-empty">Каталог пуст. Профили используют legacy fallback.</p>}
      {catalog.data?.items.map((model) => <article className="admin-model-card" key={model.key}>
        <strong>{model.display_name}</strong><p className="admin-mono">{model.key}</p>
        <p>{model.enabled ? 'Включена' : 'Выключена'} · Профилей: {model.used_by_profiles.length}</p>
        <ModelInfo model={model} />
        <p className="admin-mono">Aliases: {model.aliases.join(', ') || 'нет'}</p>
        {editor?.model?.key === model.key ? <ModelEditor key={model.key} model={editor.model} onSaved={onSaved} onCancel={() => setEditor(null)} /> : <button type="button" onClick={() => { setEditor({ model }); setSaved(false) }}>Изменить модель {model.display_name}</button>}
      </article>)}
      <p className="admin-footnote">Фактические вызовы, токены и расходы по physical model доступны ниже в «Разбивке расходов».</p>
    </>}
  </section>
}
