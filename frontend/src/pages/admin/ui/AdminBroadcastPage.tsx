import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useMemo, useState } from 'react'

import { usePageTitle } from '@/shared/lib/use-page-title'

import {
  cancelAdminBroadcast,
  getAdminBroadcastHistory,
  getAdminBroadcastProgress,
  previewAdminBroadcast,
  resumeAdminBroadcast,
  startAdminBroadcast,
} from '../api/admin-broadcast'

type Stage = 1 | 2 | 3 | 4 | 5 | 6

const stages = ['Контент', 'Аудитория', 'Preview', 'Подтверждение', 'Отправка', 'Результат']

type BroadcastPayload = {
  body: string
  activeDays: number
  mediaMode: 'text' | 'photo'
  photo?: File
  chatIds?: number[]
}

type BroadcastAttempt = {
  payload: BroadcastPayload
  requestKey: string
  previewToken: string
}

function sameBroadcastPayload(first: BroadcastPayload, second: BroadcastPayload) {
  const sameAudience = first.chatIds === undefined && second.chatIds === undefined
    || first.chatIds !== undefined && second.chatIds !== undefined
      && first.chatIds.length === second.chatIds.length
      && first.chatIds.every((id, index) => id === second.chatIds?.[index])
  return first.body === second.body
    && first.activeDays === second.activeDays
    && first.mediaMode === second.mediaMode
    && first.photo === second.photo
    && sameAudience
}

function idempotencyKey() {
  if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) return crypto.randomUUID().replaceAll('-', '')
  return `${Date.now()}_${Math.random().toString(36).slice(2)}`
}

export function AdminBroadcastPage() {
  const queryClient = useQueryClient()
  const [stage, setStage] = useState<Stage>(1)
  const [body, setBody] = useState('')
  const [mediaMode, setMediaMode] = useState<'text' | 'photo'>('text')
  const [photo, setPhoto] = useState<File | undefined>()
  const [activeDays, setActiveDays] = useState(3)
  const [selectionMode, setSelectionMode] = useState<'all' | 'selected'>('all')
  const [selectedIds, setSelectedIds] = useState<number[]>([])
  const [candidatePreview, setCandidatePreview] = useState<Awaited<ReturnType<typeof previewAdminBroadcast>> | null>(null)
  const [previewData, setPreviewData] = useState<Awaited<ReturnType<typeof previewAdminBroadcast>> | null>(null)
  const [confirmed, setConfirmed] = useState(false)
  const [broadcastId, setBroadcastId] = useState<number | null>(null)
  const [requestKey, setRequestKey] = useState(idempotencyKey)
  const [failedPayload, setFailedPayload] = useState<BroadcastPayload | null>(null)
  usePageTitle('Рассылка · Selara Admin')

  const audienceIds = selectionMode === 'selected' ? selectedIds : undefined
  const currentPayload = (): BroadcastPayload => ({
    body,
    activeDays,
    mediaMode,
    photo,
    chatIds: audienceIds ? [...audienceIds].sort((first, second) => first - second) : undefined,
  })
  const changedSinceFailedAttempt = failedPayload !== null && !sameBroadcastPayload(failedPayload, currentPayload())
  const preview = useMutation({
    mutationFn: ({ chatIds, loadCandidates }: { chatIds?: number[]; loadCandidates?: boolean }) =>
      previewAdminBroadcast({ body, active_since_days: activeDays, chat_ids: loadCandidates ? undefined : chatIds, media_mode: mediaMode, photo }),
    onSuccess: (result, variables) => {
      if (variables.loadCandidates) {
        setCandidatePreview(result)
        return
      }
      setPreviewData(result)
      setStage(3)
    },
  })
  const send = useMutation({
    mutationFn: (attempt: BroadcastAttempt) => startAdminBroadcast({
      body: attempt.payload.body,
      active_since_days: attempt.payload.activeDays,
      chat_ids: attempt.payload.chatIds,
      media_mode: attempt.payload.mediaMode,
      photo: attempt.payload.photo,
      confirm: true,
      idempotency_key: attempt.requestKey,
      preview_token: attempt.previewToken,
    }),
    onError: (_error, attempt) => setFailedPayload((current) => current ?? attempt.payload),
    onSuccess: (result) => {
      setFailedPayload(null)
      setBroadcastId(result.broadcast_id)
      setStage(5)
      void queryClient.invalidateQueries({ queryKey: ['miniapp-admin-broadcast-history'] })
    },
  })
  const progress = useQuery({
    queryKey: ['miniapp-admin-broadcast', broadcastId],
    queryFn: ({ signal }) => getAdminBroadcastProgress(broadcastId!, signal),
    enabled: broadcastId !== null,
    refetchInterval: (query) => query.state.data?.status === 'sending' ? 1200 : false,
    staleTime: 0,
  })
  const history = useInfiniteQuery({
    queryKey: ['miniapp-admin-broadcast-history'],
    initialPageParam: null as number | null,
    queryFn: ({ signal, pageParam }) => getAdminBroadcastHistory(
      { limit: 10, ...(pageParam ? { before_id: pageParam } : {}) },
      signal,
    ),
    getNextPageParam: (page) => page.next_cursor ?? undefined,
    enabled: stage === 1,
    staleTime: 30_000,
  })

  const refreshBroadcast = () => {
    void queryClient.invalidateQueries({ queryKey: ['miniapp-admin-broadcast', broadcastId] })
    void queryClient.invalidateQueries({ queryKey: ['miniapp-admin-broadcast-history'] })
  }
  const resume = useMutation({
    mutationFn: () => resumeAdminBroadcast(broadcastId!),
    onSuccess: refreshBroadcast,
  })
  const cancel = useMutation({
    mutationFn: () => cancelAdminBroadcast(broadcastId!),
    onSuccess: refreshBroadcast,
  })

  const previewHtml = useMemo(() => ({ __html: previewData?.rendered_text ?? '' }), [previewData?.rendered_text])
  const photoUrl = useMemo(() => photo ? URL.createObjectURL(photo) : null, [photo])
  useEffect(() => () => { if (photoUrl) URL.revokeObjectURL(photoUrl) }, [photoUrl])
  const shownStage: Stage = stage === 5 && progress.data && progress.data.status !== 'sending' ? 6 : stage

  function runPreview() {
    setPreviewData(null)
    preview.mutate({ chatIds: audienceIds })
  }

  return (
    <section className="admin-page admin-broadcast">
      <header className="admin-page__heading">
        <p className="admin-eyebrow">Рассылка в активные группы</p>
        <h1>Новая рассылка</h1>
      </header>
      <ol className="admin-broadcast-steps" aria-label="Этапы рассылки">
        {stages.map((label, index) => <li key={label} className={shownStage === index + 1 ? 'is-current' : shownStage > index + 1 ? 'is-done' : ''}><span>{index + 1}</span><small>{label}</small></li>)}
      </ol>

      {stage === 1 ? (
        <>
        <div className="admin-broadcast-form">
          <label htmlFor="broadcast-content">Текст Telegram-сообщения</label>
          <textarea id="broadcast-content" rows={9} maxLength={5000} value={body} onChange={(event) => setBody(event.target.value)} placeholder={'Привет!\n\nНовости Selara…\n\n[reactions]\n👍=Полезно\n👀=Посмотрю позже\n[/reactions]'} />
          <div className="admin-filter-row" role="group" aria-label="Формат рассылки">
            <button type="button" className={mediaMode === 'text' ? 'is-selected' : ''} onClick={() => { setMediaMode('text'); setPhoto(undefined) }}>Текст</button>
            <button type="button" className={mediaMode === 'photo' ? 'is-selected' : ''} onClick={() => setMediaMode('photo')}>Фото</button>
          </div>
          {mediaMode === 'photo' ? <label className="admin-photo-input">Изображение<input type="file" accept="image/jpeg,image/png" onChange={(event) => setPhoto(event.target.files?.[0])} /></label> : null}
          <p className="admin-footnote">Поддерживается Telegram HTML, блок реакций и фотография из существующей рассылки.</p>
          <div className="admin-broadcast-actions"><button className="admin-primary-action" type="button" disabled={!body.trim()} onClick={() => setStage(2)}>Далее: аудитория</button></div>
        </div>
        <section className="admin-broadcast-history">
          <h2>Последние рассылки</h2>
          {history.isPending ? <div className="admin-skeleton-list"><i /><i /></div> : null}
          {history.isError ? <div className="admin-inline-error">Не удалось загрузить историю. <button type="button" onClick={() => void history.refetch()}>Повторить</button></div> : null}
          {history.data?.pages.flatMap((page) => page.items).map((item) => (
            <article key={item.id}><strong>#{item.id} · {item.target_count} групп</strong><p>{item.body.slice(0, 130)}</p><small>{new Date(item.created_at).toLocaleString('ru-RU')} · успешно {item.sent_count} · ошибок {item.failed_count}{item.pending_count ? ` · в очереди ${item.pending_count}` : ''}</small>{item.pending_count ? <button type="button" onClick={() => { setBroadcastId(item.id); setStage(5) }}>Открыть</button> : null}</article>
          ))}
          {history.hasNextPage ? <button className="admin-load-more" type="button" disabled={history.isFetchingNextPage} onClick={() => void history.fetchNextPage()}>{history.isFetchingNextPage ? 'Загружаю…' : 'Загрузить ещё'}</button> : null}
        </section>
        </>
      ) : null}

      {stage === 2 ? (
        <div className="admin-broadcast-form">
          <label htmlFor="active-days">Активность группы за последние дни</label>
          <select id="active-days" value={activeDays} onChange={(event) => { setActiveDays(Number(event.target.value)); setCandidatePreview(null); setSelectedIds([]) }}>
            {[1, 3, 7, 14, 30, 90].map((days) => <option key={days} value={days}>{days} {days === 1 ? 'день' : 'дней'}</option>)}
          </select>
          <div className="admin-filter-row" role="group" aria-label="Выбор групп">
            <button type="button" className={selectionMode === 'all' ? 'is-selected' : ''} onClick={() => setSelectionMode('all')}>Все активные группы</button>
            <button type="button" className={selectionMode === 'selected' ? 'is-selected' : ''} onClick={() => setSelectionMode('selected')}>Выбрать группы</button>
          </div>
          {selectionMode === 'selected' ? (
            candidatePreview?.targets.length && !candidatePreview.targets_truncated ? (
              <div className="admin-broadcast-targets">
                {candidatePreview.targets.map((target) => (
                  <label key={target.chat_id}><input type="checkbox" checked={selectedIds.includes(target.chat_id)} onChange={(event) => setSelectedIds((current) => event.target.checked ? [...current, target.chat_id] : current.filter((id) => id !== target.chat_id))} /><span>{target.title || `Группа ${target.chat_id}`}</span></label>
                ))}
              </div>
            ) : candidatePreview?.targets_truncated ? (
              <p className="admin-footnote">Список превышает 100 групп. Для выборочной рассылки используйте старую панель.</p>
            ) : (
              <div>
                <p className="admin-footnote">Загрузите список, затем отметьте группы. Если ничего не выбрано, рассылка не продолжится.</p>
                <button type="button" disabled={preview.isPending} onClick={() => preview.mutate({ loadCandidates: true })}>{preview.isPending ? 'Загружаю…' : 'Загрузить список групп'}</button>
              </div>
            )
          ) : null}
          {preview.isError ? <div className="admin-inline-error">{preview.error.message}</div> : null}
          <div className="admin-broadcast-actions">
            <button type="button" onClick={() => setStage(1)}>Назад</button>
            <button className="admin-primary-action" type="button" disabled={preview.isPending || (selectionMode === 'selected' && selectedIds.length === 0)} onClick={runPreview}>{preview.isPending ? 'Готовлю preview…' : selectionMode === 'selected' && selectedIds.length === 0 ? 'Выберите хотя бы одну группу' : 'Показать preview'}</button>
          </div>
        </div>
      ) : null}

      {stage === 3 && previewData ? (
        <div className="admin-broadcast-form">
          <div className="admin-preview-meta"><strong>{previewData.target_count}</strong><span>групп получат сообщение</span></div>
          <article className="admin-telegram-preview">
            <small>Selara · предварительный просмотр</small>
            {previewData.media && photoUrl ? <img className="admin-preview-image" src={photoUrl} alt="Фото в рассылке" /> : null}
            <div dangerouslySetInnerHTML={previewHtml} />
            {previewData.reaction_options.length ? <div className="admin-preview-reactions">{previewData.reaction_options.map((option) => <span key={option.key}>{option.emoji} {option.label}</span>)}</div> : null}
          </article>
          <div className="admin-broadcast-actions"><button type="button" onClick={() => setStage(1)}>Изменить содержимое</button><button type="button" onClick={() => setStage(2)}>Изменить аудиторию</button><button className="admin-primary-action" type="button" onClick={() => { setConfirmed(false); setStage(4) }}>Перейти к подтверждению</button></div>
        </div>
      ) : null}

      {stage === 4 ? (
        <div className="admin-broadcast-form">
          <p>Будет отправлено в <strong>{previewData?.target_count ?? 0} активных групп</strong>. После подтверждения сообщение начнёт уходить сразу.</p>
          <label className="admin-confirm-check"><input type="checkbox" checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} /><span>Я проверил текст и аудиторию, подтверждаю отправку.</span></label>
          {send.isError ? <div className="admin-inline-error">{send.error.message}</div> : null}
          {send.isError && changedSinceFailedAttempt ? (
            <div className="admin-broadcast-recovery">
              <p>Содержимое или аудитория отличаются от предыдущей попытки. Сервер мог принять её, даже если ответ не дошёл. Для отдельной отправки изменённого варианта создайте новый ключ и подтвердите его ещё раз.</p>
              <button type="button" onClick={() => { setRequestKey(idempotencyKey()); setFailedPayload(null); send.reset(); setConfirmed(false) }}>Использовать новый ключ для изменённой рассылки</button>
            </div>
          ) : null}
          <div className="admin-broadcast-actions"><button type="button" onClick={() => setStage(3)}>Назад к preview</button><button className="admin-primary-action" type="button" disabled={!confirmed || send.isPending || changedSinceFailedAttempt} onClick={() => send.mutate({ payload: currentPayload(), requestKey, previewToken: previewData?.preview_token ?? '' })}>{send.isPending ? 'Запускаю…' : 'Подтвердить отправку'}</button></div>
        </div>
      ) : null}

      {stage === 5 && progress.data?.status === 'sending' ? (
        <div className="admin-broadcast-progress">
          <p className="admin-eyebrow">Рассылка #{progress.data.broadcast_id}</p>
          <h2>{progress.data.status === 'sending' ? 'Отправка' : 'Обновляю результат'}</h2>
          <strong>{progress.data.sent_count} / {progress.data.target_count}</strong>
          <progress max={Math.max(progress.data.target_count, 1)} value={progress.data.sent_count + progress.data.failed_count} />
          <p>Успешно {progress.data.sent_count} · ошибок {progress.data.failed_count} · осталось {progress.data.pending_count}</p>
          {progress.isError ? <button type="button" onClick={() => void progress.refetch()}>Обновить прогресс</button> : null}
          <button type="button" disabled={cancel.isPending} onClick={() => cancel.mutate()}>{cancel.isPending ? 'Отменяю…' : 'Отменить оставшиеся'}</button>
          {cancel.isError ? <div className="admin-inline-error">{cancel.error.message}</div> : null}
        </div>
      ) : null}

      {stage === 5 && !progress.data && progress.isError ? <div className="admin-inline-error">Не удалось загрузить состояние рассылки. <button type="button" onClick={() => void progress.refetch()}>Повторить</button></div> : null}
      {stage === 5 && !progress.data && progress.isPending ? <div className="admin-skeleton-list"><i /><i /></div> : null}

      {shownStage === 6 && progress.data ? (
        <div className="admin-broadcast-progress">
          <p className="admin-eyebrow">Рассылка #{progress.data.broadcast_id}</p>
          <h2>{progress.data.status === 'completed' ? 'Готово' : progress.data.status === 'cancelled' ? 'Рассылка отменена' : 'Отправка остановлена'}</h2>
          <dl><div><dt>Успешно</dt><dd>{progress.data.sent_count}</dd></div><div><dt>Ошибок</dt><dd>{progress.data.failed_count}</dd></div><div><dt>Пропущено</dt><dd>{progress.data.skipped_count}</dd></div></dl>
          {progress.data.duration_seconds !== null ? <p className="admin-footnote">Длительность: {progress.data.duration_seconds} сек.</p> : null}
          {progress.data.status === 'interrupted' ? (
            <>
              <p className="admin-footnote">Сообщения, отправленные до остановки, повторно не уйдут. Продолжение отправит только оставшиеся.</p>
              <div className="admin-broadcast-actions">
                <button className="admin-primary-action" type="button" disabled={resume.isPending} onClick={() => resume.mutate()}>{resume.isPending ? 'Продолжаю…' : 'Продолжить рассылку'}</button>
                <button type="button" disabled={cancel.isPending} onClick={() => cancel.mutate()}>Отменить оставшиеся</button>
              </div>
              {resume.isError ? <div className="admin-inline-error">{resume.error.message}</div> : null}
              {cancel.isError ? <div className="admin-inline-error">{cancel.error.message}</div> : null}
            </>
          ) : null}
          {progress.data.status === 'cancelled' ? <p className="admin-footnote">Оставшиеся сообщения не отправлены.</p> : null}
          <button className="admin-primary-action" type="button" onClick={() => { setStage(1); setBody(''); setPreviewData(null); setBroadcastId(null); setConfirmed(false); setPhoto(undefined); setMediaMode('text'); setSelectedIds([]); setSelectionMode('all'); setRequestKey(idempotencyKey()) }}>Новая рассылка</button>
        </div>
      ) : null}
    </section>
  )
}
