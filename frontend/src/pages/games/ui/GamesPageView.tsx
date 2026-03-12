import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'

import type {
  GamesButton,
  GamesCard,
  GamesCategoryOption,
  GamesPageData,
  GamesThemePicker,
} from '@/pages/games/model/types'
import { routes } from '@/shared/config/routes'

import './games-page.css'

type GamesPageViewProps = {
  data: GamesPageData
  onRefresh: () => void
  isRefreshing: boolean
  feedbackMessage: string | null
  isMutating: boolean
  onCreateGame: (payload: {
    kind: string
    chat_id: string
    spy_category?: string
    whoami_category?: string
    zlob_category?: string
  }) => Promise<void>
  onGameAction: (payload: Record<string, string | number | boolean | null | undefined>) => Promise<void>
}

function renderOptionLabel(option: GamesCategoryOption) {
  const parts = [option.label]
  if (option.count) {
    parts.push(option.count)
  }
  if (option.is_18_plus) {
    parts.push('18+')
  }
  return parts.join(' • ')
}

function ActionButtons({
  buttons,
  disabled,
  onAction,
}: {
  buttons: GamesButton[]
  disabled: boolean
  onAction: (callbackData: string) => void
}) {
  if (buttons.length === 0) {
    return null
  }

  return (
    <div className="games-actions-row">
      {buttons.map((button) =>
        button.kind === 'url' && button.url ? (
          <a key={`${button.label}-${button.url}`} className="button" href={button.url} target="_blank" rel="noreferrer">
            {button.label}
          </a>
        ) : (
          <button
            key={`${button.label}-${button.callback_data}`}
            className="button"
            type="button"
            disabled={disabled || !button.callback_data}
            onClick={() => {
              if (button.callback_data) {
                onAction(button.callback_data)
              }
            }}
          >
            {button.label}
          </button>
        ),
      )}
    </div>
  )
}

function ThemePicker({
  label,
  picker,
  action,
  disabled,
  onSubmit,
}: {
  label: string
  picker: GamesThemePicker
  action: string
  disabled: boolean
  onSubmit: (value: string) => void
}) {
  const [value, setValue] = useState(picker.current_value)

  return (
    <form
      className="games-inline-form"
      onSubmit={(event) => {
        event.preventDefault()
        onSubmit(value)
      }}
    >
      <span className="games-form-label">{label}</span>
      <select value={value} onChange={(event) => setValue(event.target.value)}>
        {picker.options.map((option) => (
          <option key={`${action}-${option.value}`} value={option.value}>
            {renderOptionLabel(option)}
          </option>
        ))}
      </select>
      <button className="button" type="submit" disabled={disabled}>
        Применить
      </button>
    </form>
  )
}

function GameCardView({
  game,
  disabled,
  onAction,
}: {
  game: GamesCard
  disabled: boolean
  onAction: (payload: Record<string, string | number | boolean | null | undefined>) => void
}) {
  const [numberGuess, setNumberGuess] = useState('')
  const [bredAnswer, setBredAnswer] = useState('')
  const [spyGuess, setSpyGuess] = useState(game.spy_view?.guess_form?.locations[0] ?? '')
  const [whoamiQuestion, setWhoamiQuestion] = useState('')
  const [whoamiGuess, setWhoamiGuess] = useState('')
  const [zlobSelection, setZlobSelection] = useState<string[]>([])

  return (
    <article className={`games-card games-card--${game.kind}`}>
      <div className="games-card__head">
        <div>
          <h3>{game.title}</h3>
          <p>{game.description}</p>
        </div>
        <div className="games-card__tags">
          <span className={`games-pill games-pill--${game.status_badge}`}>{game.status}</span>
          {game.is_member ? <span className="games-pill games-pill--user">в составе</span> : null}
          {game.can_manage_games ? <span className="games-pill games-pill--admin">ведущий</span> : null}
        </div>
      </div>

      <div className="games-card__meta">
        <div className="games-meta-card">
          <span className="games-meta-card__label">Чат</span>
          <strong>{game.chat_title}</strong>
          <p>ID {game.chat_id}</p>
          <Link className="button" to={routes.chat(game.chat_id)}>
            Открыть чат
          </Link>
        </div>
        <div className="games-meta-card">
          <span className="games-meta-card__label">Состав</span>
          <strong>{game.players_count} участников</strong>
          <p>{game.is_member ? 'Вы в игре' : 'Режим наблюдения'}</p>
          <span>Раунд {game.round_no}</span>
        </div>
        <div className="games-meta-card">
          <span className="games-meta-card__label">Время</span>
          <strong>Старт {game.started_at}</strong>
          <p>Создана {game.created_at}</p>
          <span>{game.is_owner ? 'Вы создали лобби' : 'Создатель в Telegram'}</span>
        </div>
      </div>

      {game.players_preview.length > 0 ? (
        <div className="games-players">
          {game.players_preview.map((player) => (
            <span key={`${game.game_id}-${player}`} className="games-pill games-pill--user">
              {player}
            </span>
          ))}
          {game.players_hidden > 0 ? <span className="games-pill">+{game.players_hidden}</span> : null}
        </div>
      ) : null}

      {game.spotlight ? (
        <section className="games-spotlight">
          <div>
            <span className="games-spotlight__eyebrow">{game.spotlight.eyebrow}</span>
            <strong>{game.spotlight.title}</strong>
            <p>{game.spotlight.description}</p>
          </div>
          {game.spotlight.prompt_text ? (
            <div className="games-stage-note">
              {game.spotlight.prompt_title ? <strong>{game.spotlight.prompt_title}</strong> : null}
              <p>{game.spotlight.prompt_text}</p>
            </div>
          ) : null}
          {game.spotlight.metrics.length > 0 ? (
            <div className="games-mini-stats">
              {game.spotlight.metrics.map((metric) => (
                <div key={`${game.game_id}-${metric.label}`} className="games-mini-stat">
                  <span>{metric.label}</span>
                  <strong>{metric.value}</strong>
                </div>
              ))}
            </div>
          ) : null}
        </section>
      ) : null}

      {game.spy_theme_picker ? (
        <ThemePicker
          label="Тема Spyfall"
          picker={game.spy_theme_picker}
          action="spy_set_category"
          disabled={disabled}
          onSubmit={(value) => onAction({ action: 'spy_set_category', game_id: game.game_id, spy_category: value })}
        />
      ) : null}
      {game.whoami_theme_picker ? (
        <ThemePicker
          label="Тема Кто я?"
          picker={game.whoami_theme_picker}
          action="whoami_set_category"
          disabled={disabled}
          onSubmit={(value) => onAction({ action: 'whoami_set_category', game_id: game.game_id, whoami_category: value })}
        />
      ) : null}
      {game.zlob_theme_picker ? (
        <ThemePicker
          label="Тема Злобокарт"
          picker={game.zlob_theme_picker}
          action="zlob_set_category"
          disabled={disabled}
          onSubmit={(value) => onAction({ action: 'zlob_set_category', game_id: game.game_id, zlob_category: value })}
        />
      ) : null}

      <ActionButtons buttons={game.manage_buttons} disabled={disabled} onAction={(callbackData) => onAction({ callback_data: callbackData })} />
      <ActionButtons buttons={game.main_buttons} disabled={disabled} onAction={(callbackData) => onAction({ callback_data: callbackData })} />
      <ActionButtons buttons={game.private_buttons} disabled={disabled} onAction={(callbackData) => onAction({ callback_data: callbackData })} />
      <ActionButtons buttons={game.vote_buttons} disabled={disabled} onAction={(callbackData) => onAction({ callback_data: callbackData })} />
      <ActionButtons buttons={game.telegram_buttons} disabled={disabled} onAction={() => undefined} />

      {game.show_number_guess ? (
        <form
          className="games-inline-form"
          onSubmit={(event) => {
            event.preventDefault()
            onAction({ action: 'number_guess', game_id: game.game_id, guess: numberGuess })
          }}
        >
          <span className="games-form-label">Угадать число</span>
          <input value={numberGuess} onChange={(event) => setNumberGuess(event.target.value)} placeholder="Введите число" />
          <button className="button" type="submit" disabled={disabled}>
            Отправить
          </button>
        </form>
      ) : null}

      {game.show_bred_answer ? (
        <form
          className="games-inline-form"
          onSubmit={(event) => {
            event.preventDefault()
            onAction({ action: 'bred_submit', game_id: game.game_id, lie_text: bredAnswer })
          }}
        >
          <span className="games-form-label">Ответ для Бредовухи</span>
          <input value={bredAnswer} onChange={(event) => setBredAnswer(event.target.value)} placeholder="Ваш вариант ответа" />
          <button className="button" type="submit" disabled={disabled}>
            Сдать ответ
          </button>
        </form>
      ) : null}

      {game.spy_view ? (
        <section className="games-detail">
          <strong>{game.spy_view.status_title}</strong>
          <p>{game.spy_view.status_text}</p>
          <ActionButtons buttons={game.spy_view.action_buttons} disabled={disabled} onAction={(callbackData) => onAction({ callback_data: callbackData })} />
          {game.spy_view.guess_form ? (
            <form
              className="games-inline-form"
              onSubmit={(event) => {
                event.preventDefault()
                onAction({ action: 'spy_guess', game_id: game.game_id, guess_location: spyGuess })
              }}
            >
              <span className="games-form-label">{game.spy_view.guess_form.button_label}</span>
              <select value={spyGuess} onChange={(event) => setSpyGuess(event.target.value)}>
                {game.spy_view.guess_form.locations.map((location) => (
                  <option key={`${game.game_id}-${location}`} value={location}>
                    {location}
                  </option>
                ))}
              </select>
              <button className="button" type="submit" disabled={disabled}>
                Отправить
              </button>
            </form>
          ) : null}
        </section>
      ) : null}

      {game.whoami_view ? (
        <section className="games-detail">
          <strong>{game.whoami_view.status_title}</strong>
          <p>{game.whoami_view.status_text}</p>
          <ActionButtons buttons={game.whoami_view.action_buttons} disabled={disabled} onAction={(callbackData) => onAction({ callback_data: callbackData })} />
          {game.whoami_view.question_form ? (
            <form
              className="games-inline-form"
              onSubmit={(event) => {
                event.preventDefault()
                onAction({ action: 'whoami_ask', game_id: game.game_id, question_text: whoamiQuestion })
              }}
            >
              <span className="games-form-label">{game.whoami_view.question_form.button_label}</span>
              <input value={whoamiQuestion} onChange={(event) => setWhoamiQuestion(event.target.value)} placeholder={game.whoami_view.question_form.placeholder} />
              <button className="button" type="submit" disabled={disabled}>
                Отправить
              </button>
            </form>
          ) : null}
          {game.whoami_view.guess_form ? (
            <form
              className="games-inline-form"
              onSubmit={(event) => {
                event.preventDefault()
                onAction({ action: 'whoami_guess', game_id: game.game_id, guess_text: whoamiGuess })
              }}
            >
              <span className="games-form-label">{game.whoami_view.guess_form.button_label}</span>
              <input value={whoamiGuess} onChange={(event) => setWhoamiGuess(event.target.value)} placeholder={game.whoami_view.guess_form.placeholder} />
              <button className="button" type="submit" disabled={disabled}>
                Проверить
              </button>
            </form>
          ) : null}
        </section>
      ) : null}

      {game.mafia_view ? (
        <section className="games-detail">
          <strong>{game.mafia_view.status_title}</strong>
          <p>{game.mafia_view.status_text}</p>
          <ActionButtons buttons={game.mafia_view.action_buttons} disabled={disabled} onAction={(callbackData) => onAction({ callback_data: callbackData })} />
        </section>
      ) : null}

      {game.zlob_view ? (
        <section className="games-detail">
          <strong>{game.zlob_view.status_title}</strong>
          <p>{game.zlob_view.status_text}</p>
          <ActionButtons buttons={game.zlob_view.submit_buttons} disabled={disabled} onAction={(callbackData) => onAction({ callback_data: callbackData })} />
          <ActionButtons buttons={game.zlob_view.vote_buttons} disabled={disabled} onAction={(callbackData) => onAction({ callback_data: callbackData })} />
          {game.zlob_view.submit_form ? (
            <form
              className="games-zlob-form"
              onSubmit={(event) => {
                event.preventDefault()
                onAction({
                  action: 'zlob_submit',
                  game_id: game.game_id,
                  card_index: zlobSelection[0] ?? '',
                  card_index_second: zlobSelection[1] ?? '',
                })
              }}
            >
              <span className="games-form-label">Сдать карты</span>
              {game.zlob_view.submit_form.hand.map((card) => {
                const checked = zlobSelection.includes(card.index)
                return (
                  <label key={`${game.game_id}-${card.index}`} className="games-check-card">
                    <input
                      type="checkbox"
                      checked={checked}
                      onChange={(event) => {
                        setZlobSelection((current) => {
                          const next = event.target.checked
                            ? [...current, card.index]
                            : current.filter((item) => item !== card.index)
                          return next.slice(0, game.zlob_view?.submit_form?.slots ?? 1)
                        })
                      }}
                    />
                    <span>{card.text}</span>
                  </label>
                )
              })}
              <button className="button" type="submit" disabled={disabled || zlobSelection.length === 0}>
                Сдать выбор
              </button>
            </form>
          ) : null}
        </section>
      ) : null}

      {game.score_rows.length > 0 ? (
        <div className="games-score-grid">
          {game.score_rows.map((row) => (
            <div key={`${game.game_id}-${row.position}-${row.label}`} className="games-mini-stat">
              <span>
                {row.position} · {row.label}
              </span>
              <strong>{row.value}</strong>
            </div>
          ))}
        </div>
      ) : null}

      {game.bred_submission_rows.length > 0 ? (
        <div className="games-status-grid">
          {game.bred_submission_rows.map((row) => (
            <div key={`${game.game_id}-${row.label}`} className="games-stage-note">
              <strong>{row.label}</strong>
              <p>{row.state_label}</p>
            </div>
          ))}
        </div>
      ) : null}

      {game.bred_reveal_rows.length > 0 ? (
        <div className="games-status-grid">
          {game.bred_reveal_rows.map((row) => (
            <div key={`${game.game_id}-${row.slot}-${row.text}`} className="games-stage-note">
              <strong>
                {row.slot} · {row.author ?? '—'}
              </strong>
              <p>{row.text}</p>
            </div>
          ))}
        </div>
      ) : null}
    </article>
  )
}

export function GamesPageView({
  data,
  onRefresh,
  isRefreshing,
  feedbackMessage,
  isMutating,
  onCreateGame,
  onGameAction,
}: GamesPageViewProps) {
  const firstAvailableChat = data.create_chat_options[0]?.chat_id ?? ''
  const [createDraft, setCreateDraft] = useState({
    kind: data.default_create_kind || data.game_catalog[0]?.key || '',
    chat_id: firstAvailableChat,
    spy_category: '',
    whoami_category: '',
    zlob_category: '',
  })
  const selectedGame = useMemo(
    () => data.game_catalog.find((item) => item.key === createDraft.kind) ?? data.default_create_game,
    [createDraft.kind, data.default_create_game, data.game_catalog],
  )
  const selectedChat = useMemo(
    () => data.create_chat_options.find((chat) => chat.chat_id === createDraft.chat_id) ?? null,
    [createDraft.chat_id, data.create_chat_options],
  )
  const chatAllows18 = selectedChat?.actions_18_enabled !== 'false'
  const visibleSpyOptions = useMemo(
    () => data.spy_category_options.filter((option) => chatAllows18 || !option.is_18_plus),
    [chatAllows18, data.spy_category_options],
  )
  const visibleWhoamiOptions = useMemo(
    () => data.whoami_category_options.filter((option) => chatAllows18 || !option.is_18_plus),
    [chatAllows18, data.whoami_category_options],
  )
  const visibleZlobOptions = useMemo(
    () => data.zlob_category_options.filter((option) => chatAllows18 || !option.is_18_plus),
    [chatAllows18, data.zlob_category_options],
  )
  const effectiveSpyCategory = visibleSpyOptions.some((option) => option.value === createDraft.spy_category)
    ? createDraft.spy_category
    : visibleSpyOptions[0]?.value ?? ''
  const effectiveWhoamiCategory = visibleWhoamiOptions.some((option) => option.value === createDraft.whoami_category)
    ? createDraft.whoami_category
    : visibleWhoamiOptions[0]?.value ?? ''
  const effectiveZlobCategory = visibleZlobOptions.some((option) => option.value === createDraft.zlob_category)
    ? createDraft.zlob_category
    : visibleZlobOptions[0]?.value ?? ''

  return (
    <div className="games-page">
      <section className="games-hero">
        <div>
          <span className="page-card__eyebrow">Selara Party</span>
          <h1>{data.hero_title}</h1>
          <p>{data.hero_subtitle}</p>
          <div className="games-hero__actions">
            <a className="button" href="#games-active">
              К партиям
            </a>
            <button className="button button--primary" type="button" onClick={onRefresh} disabled={isRefreshing || isMutating}>
              {isRefreshing ? 'Обновляю…' : 'Обновить сцену'}
            </button>
          </div>
        </div>
        <div className="games-hero__chips">
          <span className="games-chip">Блеф и дедукция</span>
          <span className="games-chip">Живые партии</span>
          <span className="games-chip">Telegram и браузер</span>
        </div>
      </section>

      {feedbackMessage ? <section className="games-banner">{feedbackMessage}</section> : null}

      <section className="games-metrics">
        {data.metrics.map((metric) => (
          <article key={metric.label} className={`games-metric games-metric--${metric.tone}`}>
            <span className="games-metric__label">{metric.label}</span>
            <strong className="games-metric__value">{metric.value}</strong>
            <span className="games-metric__note">{metric.note}</span>
          </article>
        ))}
      </section>

      <section className="games-panel">
        <div className="games-panel__head">
          <div>
            <h2>Создание игры</h2>
            <p>Лобби открывается прямо в браузере через текущий серверный сценарий.</p>
          </div>
        </div>
        {selectedGame ? (
          <div className={`games-stage-note games-stage-note--${selectedGame.tone}`}>
            <strong>{selectedGame.title}</strong>
            <p>{selectedGame.note}</p>
          </div>
        ) : null}
        <form
          className="games-create-grid"
          onSubmit={(event) => {
            event.preventDefault()
            void onCreateGame({
              ...createDraft,
              spy_category: effectiveSpyCategory,
              whoami_category: effectiveWhoamiCategory,
              zlob_category: effectiveZlobCategory,
            })
          }}
        >
          <select
            value={createDraft.chat_id}
            onChange={(event) => setCreateDraft((current) => ({ ...current, chat_id: event.target.value }))}
          >
            {data.create_chat_options.map((chat) => (
              <option key={chat.chat_id} value={chat.chat_id}>
                {chat.title}
              </option>
            ))}
          </select>
          <select
            value={createDraft.kind}
            onChange={(event) => setCreateDraft((current) => ({ ...current, kind: event.target.value }))}
          >
            {data.game_catalog.map((item) => (
              <option key={item.key} value={item.key}>
                {item.title}
              </option>
            ))}
          </select>
          {createDraft.kind === 'spy' ? (
            <select
              value={effectiveSpyCategory}
              onChange={(event) => setCreateDraft((current) => ({ ...current, spy_category: event.target.value }))}
            >
              {visibleSpyOptions.map((option) => (
                <option key={`spy-${option.value}`} value={option.value}>
                  {renderOptionLabel(option)}
                </option>
              ))}
            </select>
          ) : null}
          {createDraft.kind === 'whoami' ? (
            <select
              value={effectiveWhoamiCategory}
              onChange={(event) => setCreateDraft((current) => ({ ...current, whoami_category: event.target.value }))}
            >
              {visibleWhoamiOptions.map((option) => (
                <option key={`whoami-${option.value}`} value={option.value}>
                  {renderOptionLabel(option)}
                </option>
              ))}
            </select>
          ) : null}
          {createDraft.kind === 'zlobcards' ? (
            <select
              value={effectiveZlobCategory}
              onChange={(event) => setCreateDraft((current) => ({ ...current, zlob_category: event.target.value }))}
            >
              {visibleZlobOptions.map((option) => (
                <option key={`zlob-${option.value}`} value={option.value}>
                  {renderOptionLabel(option)}
                </option>
              ))}
            </select>
          ) : null}
          <button className="button button--primary" type="submit" disabled={isMutating || !createDraft.chat_id || !createDraft.kind}>
            Открыть лобби
          </button>
        </form>
        {data.busy_create_chat_options.length > 0 ? (
          <div className="games-stage-note">
            <strong>Занятые чаты</strong>
            <p>{data.busy_create_chat_options.map((item) => item.title).join(' • ')}</p>
          </div>
        ) : null}
        {!chatAllows18 ? (
          <div className="games-stage-note">
            <strong>Безопасный режим тем</strong>
            <p>Для выбранного чата 18+ темы скрыты, поэтому доступны только безопасные наборы.</p>
          </div>
        ) : null}
      </section>

      <section id="games-active" className="games-list">
        <div className="games-panel__head">
          <div>
            <h2>Активные сессии</h2>
            <p>Управление фазами и игровые действия уже доступны прямо на этой странице.</p>
          </div>
        </div>

        {data.game_cards.length > 0 ? (
          <div className="games-cards">
            {data.game_cards.map((game) => (
              <GameCardView
                key={game.game_id}
                game={game}
                disabled={isMutating}
                onAction={(payload) => {
                  void onGameAction(payload)
                }}
              />
            ))}
          </div>
        ) : (
          <div className="games-stage-note">
            <strong>Активных игр сейчас нет</strong>
            <p>Когда появятся лобби или запущенные сессии, они будут показаны здесь.</p>
          </div>
        )}
      </section>

      <section className="games-panel">
        <div className="games-panel__head">
          <div>
            <h2>Недавние партии</h2>
            <p>Короткий список завершённых или последних видимых игр.</p>
          </div>
        </div>

        {data.recent_game_cards.length > 0 ? (
          <div className="games-recent-list">
            {data.recent_game_cards.map((game) => (
              <div key={game.game_id} className="games-recent-card">
                <div>
                  <strong>{game.title}</strong>
                  <p>{game.chat_title}</p>
                </div>
                <div className="games-recent-card__meta">
                  <span>{game.started_at}</span>
                  <p>{game.result_text}</p>
                </div>
              </div>
            ))}
          </div>
        ) : (
          <div className="games-stage-note">
            <strong>Недавних партий пока нет</strong>
            <p>После первых завершённых сессий здесь появится история.</p>
          </div>
        )}
      </section>
    </div>
  )
}
