import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'

import type {
  GamesBadge,
  GamesButton,
  GamesCard,
  GamesCategoryOption,
  GamesPageData,
  GamesRevealRow,
  GamesRoleRevealRow,
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

function buttonClassName(variant?: string) {
  if (variant === 'primary') {
    return 'button button--primary'
  }

  if (variant === 'danger') {
    return 'button button--danger'
  }

  return 'button button--secondary'
}

function BadgeRow({ badges }: { badges: GamesBadge[] }) {
  if (badges.length === 0) {
    return null
  }

  return (
    <div className="games-badge-row">
      {badges.map((badge) => (
        <span key={`${badge.label}-${badge.tone}`} className={`games-inline-badge games-inline-badge--${badge.tone}`}>
          {badge.label}
        </span>
      ))}
    </div>
  )
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
          <a
            key={`${button.label}-${button.url}`}
            className={buttonClassName(button.variant)}
            href={button.url}
            target="_blank"
            rel="noreferrer"
          >
            {button.label}
          </a>
        ) : (
          <button
            key={`${button.label}-${button.callback_data}`}
            className={buttonClassName(button.variant)}
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
      <button className="button button--secondary" type="submit" disabled={disabled}>
        Применить
      </button>
    </form>
  )
}

function Spotlight({ game }: { game: GamesCard }) {
  if (!game.spotlight) {
    return null
  }

  return (
    <section className="games-spotlight">
      <div className="games-spotlight__copy">
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

      {game.spotlight.reveal_card ? (
        <div className="games-reveal-card">
          <span className="games-reveal-card__eyebrow">{game.spotlight.reveal_card.eyebrow}</span>
          <strong>{game.spotlight.reveal_card.title}</strong>
          <p>{game.spotlight.reveal_card.text}</p>
          <span>{game.spotlight.reveal_card.note}</span>
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
  )
}

function RevealRows({
  title,
  subtitle,
  rows,
}: {
  title: string
  subtitle: string
  rows: GamesRevealRow[]
}) {
  if (rows.length === 0) {
    return null
  }

  return (
    <section className="games-detail">
      <div className="games-detail__head">
        <strong>{title}</strong>
        <p>{subtitle}</p>
      </div>
      <div className="games-reveal-grid">
        {rows.map((row) => (
          <div key={`${row.slot}-${row.text}-${row.author ?? 'author'}`} className={`games-reveal-row games-reveal-row--${row.tone}`}>
            <span className="games-reveal-row__slot">{row.slot}</span>
            <div className="games-reveal-row__copy">
              <strong>{row.text}</strong>
              <span>{row.author ?? '—'}</span>
            </div>
            <span className="games-reveal-row__votes">{row.votes}</span>
          </div>
        ))}
      </div>
    </section>
  )
}

function RoleRevealSection({
  note,
  rows,
}: {
  note: string
  rows: GamesRoleRevealRow[]
}) {
  if (rows.length === 0) {
    return null
  }

  return (
    <section className="games-detail games-detail--role-reveal">
      <div className="games-detail__head">
        <strong>Финальный reveal</strong>
        <p>{note}</p>
      </div>
      <div className="games-role-reveal-grid">
        {rows.map((row) => (
          <div key={`${row.player}-${row.role}`} className={`games-role-reveal games-role-reveal--${row.tone}`}>
            <div>
              <strong>{row.player}</strong>
              <p>{row.role}</p>
            </div>
            <span>{row.team}</span>
            <span>{row.winner ? 'победа' : 'финал'}</span>
          </div>
        ))}
      </div>
    </section>
  )
}

function ScoreRows({
  rows,
  title = 'Текущий счёт',
  subtitle = 'Лидеры обновляются после каждого раунда.',
}: {
  rows: GamesCard['score_rows']
  title?: string
  subtitle?: string
}) {
  if (rows.length === 0) {
    return null
  }

  return (
    <section className="games-detail">
      <div className="games-detail__head">
        <strong>{title}</strong>
        <p>{subtitle}</p>
      </div>
      <div className="games-score-grid">
        {rows.map((row) => (
          <div key={`${row.position}-${row.label}`} className="games-score-row">
            <span>{row.position}</span>
            <div>
              <strong>{row.label}</strong>
            </div>
            <span>{row.value}</span>
          </div>
        ))}
      </div>
    </section>
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
  const spyLocations = game.spy_view?.guess_form?.locations ?? []
  const effectiveSpyGuess = spyLocations.includes(spyGuess) ? spyGuess : spyLocations[0] ?? ''
  const zlobSlots = game.zlob_view?.submit_form?.slots ?? 0
  const zlobHandIndexes = new Set(game.zlob_view?.submit_form?.hand.map((item) => item.index) ?? [])
  const effectiveZlobSelection = zlobSelection.filter((item) => zlobHandIndexes.has(item)).slice(0, zlobSlots)

  return (
    <article className={`games-card games-card--${game.kind}`}>
      <div className="games-card__head">
        <div>
          <span className="page-card__eyebrow">{game.chat_title}</span>
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
          <Link className="button button--secondary" to={routes.chat(game.chat_id)}>
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

      <Spotlight game={game} />

      {game.spy_theme_picker ? (
        <ThemePicker
          key={`spy-${game.game_id}-${game.spy_theme_picker.current_value}`}
          label="Тема Spyfall"
          picker={game.spy_theme_picker}
          action="spy_set_category"
          disabled={disabled}
          onSubmit={(value) => onAction({ action: 'spy_set_category', game_id: game.game_id, spy_category: value })}
        />
      ) : null}
      {game.whoami_theme_picker ? (
        <ThemePicker
          key={`whoami-${game.game_id}-${game.whoami_theme_picker.current_value}`}
          label="Тема Кто я?"
          picker={game.whoami_theme_picker}
          action="whoami_set_category"
          disabled={disabled}
          onSubmit={(value) => onAction({ action: 'whoami_set_category', game_id: game.game_id, whoami_category: value })}
        />
      ) : null}
      {game.zlob_theme_picker ? (
        <ThemePicker
          key={`zlob-${game.game_id}-${game.zlob_theme_picker.current_value}`}
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
      <ActionButtons buttons={game.telegram_buttons} disabled={disabled} onAction={(callbackData) => onAction({ callback_data: callbackData })} />

      {game.spy_view ? (
        <>
          <section className="games-stage-grid games-stage-grid--spy">
            <article className={`games-detail games-role-card games-role-card--${game.spy_view.role_card.tone}`}>
              <div className="games-detail__head">
                <strong>{game.is_member ? 'Ваше досье' : 'Режим просмотра'}</strong>
                <p>Ключевая роль и скрытая информация вынесены в отдельную карточку.</p>
              </div>
              <div className="games-role-card__title">
                <span>{game.spy_view.role_card.team}</span>
                <strong>{game.spy_view.role_card.title}</strong>
              </div>
              <div className="games-role-card__fact">
                <span>{game.spy_view.role_card.intel_label}</span>
                <strong>{game.spy_view.role_card.intel_value}</strong>
              </div>
              <div className="games-role-card__grid">
                <div>
                  <span>Цель</span>
                  <p>{game.spy_view.role_card.objective}</p>
                </div>
                <div>
                  <span>Как играть</span>
                  <p>{game.spy_view.role_card.ability}</p>
                </div>
              </div>
            </article>

            <article className="games-detail">
              <div className="games-detail__head">
                <strong>Доска подозрений</strong>
                <p>Кого стол давит сильнее и где сейчас скапливаются голоса.</p>
              </div>
              <div className="games-ribbon">
                <span className="games-ribbon__pill">Тема: {game.spy_view.summary.category}</span>
                <span className="games-ribbon__pill">Голоса: {game.spy_view.summary.votes}</span>
                <span className="games-ribbon__pill">Большинство: {game.spy_view.summary.majority}</span>
                <span className="games-ribbon__pill">Лидер: {game.spy_view.summary.leader}</span>
              </div>
              <div className="games-suspect-grid">
                {game.spy_view.suspect_rows.map((row) => (
                  <div key={`${game.game_id}-${row.label}`} className={`games-suspect-row games-suspect-row--${row.tone}`}>
                    <div className="games-suspect-row__head">
                      <strong>{row.label}</strong>
                      <span>{row.votes}</span>
                    </div>
                    <div className="games-suspect-row__track">
                      <span style={{ width: `${row.meter}%` }} />
                    </div>
                    <BadgeRow badges={row.badges} />
                  </div>
                ))}
              </div>
            </article>
          </section>

          <section className="games-detail">
            <div className="games-detail__head">
              <strong>{game.spy_view.status_title}</strong>
              <p>{game.spy_view.status_text}</p>
            </div>
            {game.spy_view.action_title || game.spy_view.action_text ? (
              <div className="games-stage-note">
                <strong>{game.spy_view.action_title || 'Подозрение'}</strong>
                <p>{game.spy_view.action_text || 'Доска уже показывает полный ход дедукции.'}</p>
              </div>
            ) : null}
            <ActionButtons buttons={game.spy_view.action_buttons} disabled={disabled} onAction={(callbackData) => onAction({ callback_data: callbackData })} />
            {game.spy_view.guess_form ? (
              <form
                className="games-inline-form"
              onSubmit={(event) => {
                event.preventDefault()
                onAction({ action: 'spy_guess', game_id: game.game_id, guess_location: effectiveSpyGuess })
              }}
            >
              <span className="games-form-label">{game.spy_view.guess_form.button_label}</span>
              <input
                  value={effectiveSpyGuess}
                  list={`spy-locations-${game.game_id}`}
                  onChange={(event) => setSpyGuess(event.target.value)}
                  placeholder={game.spy_view.guess_form.placeholder}
                />
                <datalist id={`spy-locations-${game.game_id}`}>
                  {game.spy_view.guess_form.locations.map((location) => (
                    <option key={`${game.game_id}-${location}`} value={location} />
                  ))}
                </datalist>
                <button className="button button--primary" type="submit" disabled={disabled || !effectiveSpyGuess.trim()}>
                  Отправить
                </button>
              </form>
            ) : null}
          </section>
        </>
      ) : null}

      {game.whoami_view ? (
        <>
          <section className="games-detail">
            <div className="games-detail__head">
              <strong>{game.whoami_view.status_title}</strong>
              <p>{game.whoami_view.status_text}</p>
            </div>
            <div className="games-ribbon">
              <span className="games-ribbon__pill">Категория: {game.whoami_view.category}</span>
              <span className="games-ribbon__pill">Ходит: {game.whoami_view.current_actor}</span>
              <span className="games-ribbon__pill">
                Разгадано: {game.whoami_view.solved_count}/{game.whoami_view.players_total}
              </span>
            </div>
            <ActionButtons buttons={game.whoami_view.action_buttons} disabled={disabled} onAction={(callbackData) => onAction({ callback_data: callbackData })} />
          </section>

          {(game.whoami_view.question_form || game.whoami_view.guess_form) ? (
            <section className="games-stage-grid games-stage-grid--forms">
              {game.whoami_view.question_form ? (
                <article className="games-detail">
                  <div className="games-detail__head">
                    <strong>Вопрос о себе</strong>
                    <p>Задайте один точный вопрос, на который стол ответит коротко.</p>
                  </div>
                  <form
                    className="games-inline-form games-inline-form--stack"
                    onSubmit={(event) => {
                      event.preventDefault()
                      onAction({ action: 'whoami_ask', game_id: game.game_id, question_text: whoamiQuestion })
                    }}
                  >
                    <input
                      value={whoamiQuestion}
                      onChange={(event) => setWhoamiQuestion(event.target.value)}
                      placeholder={game.whoami_view.question_form.placeholder}
                    />
                    <button className="button button--primary" type="submit" disabled={disabled || !whoamiQuestion.trim()}>
                      {game.whoami_view.question_form.button_label}
                    </button>
                  </form>
                </article>
              ) : null}

              {game.whoami_view.guess_form ? (
                <article className="games-detail">
                  <div className="games-detail__head">
                    <strong>Догадка о карточке</strong>
                    <p>Верная догадка выводит вас из круга вопросов, но партия продолжается.</p>
                  </div>
                  <form
                    className="games-inline-form games-inline-form--stack"
                    onSubmit={(event) => {
                      event.preventDefault()
                      onAction({ action: 'whoami_guess', game_id: game.game_id, guess_text: whoamiGuess })
                    }}
                  >
                    <input
                      value={whoamiGuess}
                      onChange={(event) => setWhoamiGuess(event.target.value)}
                      placeholder={game.whoami_view.guess_form.placeholder}
                    />
                    <button className="button button--primary" type="submit" disabled={disabled || !whoamiGuess.trim()}>
                      {game.whoami_view.guess_form.button_label}
                    </button>
                  </form>
                </article>
              ) : null}
            </section>
          ) : null}

          <section className="games-stage-grid games-stage-grid--whoami">
            <article className="games-detail">
              <div className="games-detail__head">
                <strong>Карточки за столом</strong>
                <p>Свою карточку вы не видите, остальные лежат перед глазами как на столе.</p>
              </div>
              <div className="games-whoami-grid">
                {game.whoami_view.table_rows.map((row) => (
                  <div key={`${game.game_id}-${row.label}`} className={`games-whoami-card games-whoami-card--${row.tone}`}>
                    <div className="games-whoami-card__head">
                      <strong title={row.title}>{row.label}</strong>
                      <BadgeRow badges={row.badges} />
                    </div>
                    <div className="games-whoami-card__identity">{row.identity}</div>
                  </div>
                ))}
              </div>
            </article>

            <article className="games-detail">
              <div className="games-detail__head">
                <strong>Ход сцены</strong>
                <p>Последние вопросы, ответы и статусы догадок без лишнего шума.</p>
              </div>
              {game.whoami_view.pending_question ? (
                <div className="games-stage-note">
                  <strong>Активный вопрос</strong>
                  <p>{game.whoami_view.pending_question}</p>
                </div>
              ) : null}
              {game.whoami_view.history_rows.length > 0 ? (
                <div className="games-history-grid">
                  {game.whoami_view.history_rows.map((row, index) => (
                    <div key={`${game.game_id}-${row.title}-${index}`} className={`games-history-row games-history-row--${row.tone}`}>
                      <strong>{row.title}</strong>
                      <p>{row.text}</p>
                      <span>{row.meta}</span>
                    </div>
                  ))}
                </div>
              ) : (
                <div className="games-stage-note">
                  <strong>Партия только началась</strong>
                  <p>Первый вопрос появится здесь, как только текущий игрок откроет ход.</p>
                </div>
              )}
            </article>
          </section>
        </>
      ) : null}

      {game.mafia_view ? (
        <>
          <section className="games-stage-grid games-stage-grid--mafia">
            <article className={`games-detail games-role-card games-role-card--${game.mafia_view.role_card.tone}`}>
              <div className="games-detail__head">
                <strong>{game.is_member ? 'Ваша роль' : 'Режим доступа'}</strong>
                <p>Скрытая информация и краткий role briefing лежат прямо на сайте.</p>
              </div>
              <div className="games-role-card__title">
                <span>{game.mafia_view.role_card.team}</span>
                <strong>{game.mafia_view.role_card.title}</strong>
              </div>
              <div className="games-role-card__grid">
                <div>
                  <span>Цель</span>
                  <p>{game.mafia_view.role_card.objective}</p>
                </div>
                <div>
                  <span>Как играется</span>
                  <p>{game.mafia_view.role_card.ability}</p>
                </div>
              </div>
              {game.mafia_view.report_html ? (
                <div className="games-rich-html" dangerouslySetInnerHTML={{ __html: game.mafia_view.report_html }} />
              ) : null}
            </article>

            <article className="games-detail">
              <div className="games-detail__head">
                <strong>Стол и статусы</strong>
                <p>Кто ещё в игре, кто уже выбыл и на кого завязан публичный этап.</p>
              </div>
              <div className="games-roster-grid">
                {game.mafia_view.roster_rows.map((row) => (
                  <div key={`${game.game_id}-${row.label}`} className={`games-roster-row games-roster-row--${row.tone}`}>
                    <strong>{row.label}</strong>
                    <BadgeRow badges={row.badges} />
                  </div>
                ))}
              </div>
            </article>
          </section>

          <section className="games-detail">
            <div className="games-detail__head">
              <strong>{game.mafia_view.status_title}</strong>
              <p>{game.mafia_view.status_text}</p>
            </div>
            {game.mafia_view.action_title || game.mafia_view.action_text ? (
              <div className="games-stage-note">
                <strong>{game.mafia_view.action_title || 'Текущая фаза'}</strong>
                <p>{game.mafia_view.action_text || 'На этой фазе скрытых действий нет.'}</p>
              </div>
            ) : null}
            <ActionButtons buttons={game.mafia_view.action_buttons} disabled={disabled} onAction={(callbackData) => onAction({ callback_data: callbackData })} />
          </section>
        </>
      ) : null}

      {game.zlob_view ? (
        <>
          <section className="games-detail">
            <div className="games-detail__head">
              <strong>{game.zlob_view.status_title}</strong>
              <p>{game.zlob_view.status_text}</p>
            </div>
            <div className="games-ribbon">
              <span className="games-ribbon__pill">Тема: {game.zlob_view.category}</span>
              <span className="games-ribbon__pill">Раунд: {game.zlob_view.round_label}</span>
              <span className="games-ribbon__pill">Цель: {game.zlob_view.target_score}</span>
              <span className="games-ribbon__pill">Пропуски: {game.zlob_view.black_slots}</span>
            </div>
            <div className="games-black-card">
              <strong>Чёрная карточка</strong>
              <p>{game.zlob_view.black_text}</p>
            </div>
          </section>

          {game.zlob_view.submit_form ? (
            <section className="games-detail">
              <div className="games-detail__head">
                <strong>Сдать карты с веба</strong>
                <p>Для двух пропусков выберите две разные карты.</p>
              </div>
              <form
                className="games-zlob-form"
                onSubmit={(event) => {
                  event.preventDefault()
                  onAction({
                    action: 'zlob_submit',
                    game_id: game.game_id,
                    card_index: effectiveZlobSelection[0] ?? '',
                    card_index_second: effectiveZlobSelection[1] ?? '',
                  })
                }}
              >
                {game.zlob_view.submit_form.hand.map((card) => {
                  const checked = effectiveZlobSelection.includes(card.index)

                  return (
                    <label key={`${game.game_id}-${card.index}`} className={`games-check-card ${checked ? 'is-checked' : ''}`}>
                      <input
                        type="checkbox"
                      checked={checked}
                      onChange={(event) => {
                        setZlobSelection((current) => {
                          const normalized = current.filter((item) => zlobHandIndexes.has(item)).slice(0, zlobSlots)
                          const next = event.target.checked
                            ? [...normalized, card.index]
                            : normalized.filter((item) => item !== card.index)
                          return next.slice(0, zlobSlots)
                        })
                      }}
                      />
                      <span>{card.text}</span>
                    </label>
                  )
                })}
                <button
                  className="button button--primary"
                  type="submit"
                  disabled={disabled || effectiveZlobSelection.length !== game.zlob_view.submit_form.slots}
                >
                  Сдать выбор
                </button>
              </form>
            </section>
          ) : null}

          <ActionButtons buttons={game.zlob_view.submit_buttons} disabled={disabled} onAction={(callbackData) => onAction({ callback_data: callbackData })} />

          {game.zlob_view.submission_rows.length > 0 ? (
            <section className="games-detail">
              <div className="games-detail__head">
                <strong>Кто уже сдал</strong>
                <p>Состояние игроков обновляется без перезагрузки страницы.</p>
              </div>
              <div className="games-submission-grid">
                {game.zlob_view.submission_rows.map((row) => (
                  <div key={`${game.game_id}-${row.label}`} className={`games-submission-row games-submission-row--${row.state}`}>
                    <strong>{row.label}</strong>
                    <span>{row.state_label}</span>
                  </div>
                ))}
              </div>
            </section>
          ) : null}

          {game.zlob_view.vote_buttons.length > 0 ? (
            <section className="games-detail">
              <div className="games-detail__head">
                <strong>Голосование</strong>
                <p>Варианты анонимны до конца раунда.</p>
              </div>
              {game.zlob_view.voted_option_label ? (
                <p className="chat-status">Ваш текущий выбор: {game.zlob_view.voted_option_label}.</p>
              ) : null}
              <ActionButtons buttons={game.zlob_view.vote_buttons} disabled={disabled} onAction={(callbackData) => onAction({ callback_data: callbackData })} />
            </section>
          ) : null}

          <RevealRows
            title={game.zlob_view.show_vote ? 'Анонимные варианты' : 'Итог раунда'}
            subtitle={game.zlob_view.show_vote ? 'Авторы скрыты до завершения голосования.' : 'После раунда видны авторы и голоса.'}
            rows={game.zlob_view.option_rows}
          />
        </>
      ) : null}

      {game.bred_submission_rows.length > 0 || game.show_bred_answer ? (
        <section className="games-detail">
          <div className="games-detail__head">
            <strong>Сбор ответов</strong>
            <p>Здесь видно прогресс сдачи и поле для вашей лжи.</p>
          </div>
          {game.bred_submission_rows.length > 0 ? (
            <div className="games-submission-grid">
              {game.bred_submission_rows.map((row) => (
                <div key={`${game.game_id}-${row.label}`} className={`games-submission-row games-submission-row--${row.state}`}>
                  <strong>{row.label}</strong>
                  <span>{row.state_label}</span>
                </div>
              ))}
            </div>
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
              <button className="button button--primary" type="submit" disabled={disabled || !bredAnswer.trim()}>
                Сдать ответ
              </button>
            </form>
          ) : null}
        </section>
      ) : null}

      <RevealRows
        title="Кто придумал варианты"
        subtitle="После раунда видно, где была правда и кто автор каждой лжи."
        rows={game.bred_reveal_rows}
      />

      {game.secret_lines.length > 0 ? (
        <section className="games-detail">
          <div className="games-detail__head">
            <strong>Моя скрытая информация</strong>
            <p>Персональные сведения, которые не должны теряться при live-обновлении сцены.</p>
          </div>
          <div className="games-secret-list">
            {game.secret_lines.map((line) => (
              <div key={`${game.game_id}-${line}`} className="games-secret-row">
                {line}
              </div>
            ))}
          </div>
        </section>
      ) : null}

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
          <button className="button button--primary" type="submit" disabled={disabled || !numberGuess.trim()}>
            Отправить
          </button>
        </form>
      ) : null}

      {game.winner_text ? <div className="games-banner">{game.winner_text}</div> : null}

      <ScoreRows
        rows={game.score_rows}
        title={game.kind === 'bredovukha' ? 'Табло раунда' : 'Текущий счёт'}
        subtitle={game.kind === 'bredovukha' ? 'Очки после каждого раунда видны сразу.' : 'Лидеры обновляются после каждого раунда.'}
      />
      <RoleRevealSection note={game.role_reveal_note} rows={game.role_reveal_rows} />
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
            <a className="button button--secondary" href="#games-active">
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
          className="games-create-layout"
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
          <div className="games-create-fields">
            <label className="games-create-field">
              <span>Чат для запуска</span>
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
            </label>

            <label className="games-create-field">
              <span>Режим игры</span>
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
            </label>

            {createDraft.kind === 'spy' ? (
              <label className="games-create-field">
                <span>Тема Spyfall</span>
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
              </label>
            ) : null}

            {createDraft.kind === 'whoami' ? (
              <label className="games-create-field">
                <span>Тема «Кто я?»</span>
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
              </label>
            ) : null}

            {createDraft.kind === 'zlobcards' ? (
              <label className="games-create-field">
                <span>Тема злобокарт</span>
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
              </label>
            ) : null}
          </div>

          <aside className={`games-create-preview games-create-preview--${selectedGame?.tone ?? 'violet'}`}>
            <span className="page-card__eyebrow">Предпросмотр</span>
            <h3>{selectedGame?.title ?? 'Выберите режим'}</h3>
            <p>{selectedGame?.description ?? 'После выбора режима и чата карточка лобби откроется здесь же, в веб-интерфейсе.'}</p>

            <div className="games-create-preview__meta">
              <div className="games-create-preview__meta-card">
                <span>Чат</span>
                <strong>{selectedChat?.title ?? '—'}</strong>
              </div>
              <div className="games-create-preview__meta-card">
                <span>Игроки</span>
                <strong>{selectedGame?.min_players_label ?? '—'}</strong>
              </div>
              <div className="games-create-preview__meta-card">
                <span>Режим</span>
                <strong>{selectedGame?.mode_label ?? '—'}</strong>
              </div>
            </div>

            {selectedGame ? (
              <div className="games-stage-note games-stage-note--compact">
                <strong>Что важно</strong>
                <p>{selectedGame.note}</p>
              </div>
            ) : null}

            <button className="button button--primary" type="submit" disabled={isMutating || !createDraft.chat_id || !createDraft.kind}>
              Открыть лобби
            </button>
          </aside>
        </form>
        {data.busy_create_chat_options.length > 0 ? (
          <div className="games-stage-note">
            <strong>Занятые чаты</strong>
            <p>{data.busy_create_chat_options.map((item) => item.title).join(' • ')}</p>
          </div>
        ) : null}
        {!data.has_manageable_chats ? (
          <div className="games-stage-note">
            <strong>Нет доступных чатов для запуска</strong>
            <p>Создание новых игр откроется, когда у аккаунта появится хотя бы один чат с правом на запуск партий.</p>
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
            <p>Веб-карточки держат сцену глубже, чем простая копия Telegram-кнопок.</p>
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
            <p>История завершённых сессий с личными заметками, счётом и финальным reveal.</p>
          </div>
        </div>

        {data.recent_game_cards.length > 0 ? (
          <div className="games-recent-list">
            {data.recent_game_cards.map((game) => (
              <article key={game.game_id} className="games-recent-card">
                <div className="games-recent-card__summary">
                  <div className="games-recent-card__head">
                    <strong>{game.title}</strong>
                    <p>{game.chat_title}</p>
                  </div>
                  <div className="games-recent-card__result">
                    <span className="games-inline-badge games-inline-badge--turn">{game.kind}</span>
                    <strong>{game.result_text}</strong>
                    <span>{game.started_at}</span>
                  </div>
                </div>

                {game.personal_notes.length > 0 ? (
                  <div className="games-recent-notes">
                    {game.personal_notes.map((note) => (
                      <div key={`${game.game_id}-${note}`} className="games-secret-row">
                        {note}
                      </div>
                    ))}
                  </div>
                ) : null}

                <RoleRevealSection note={game.role_reveal_note} rows={game.role_reveal_rows} />
                <RevealRows
                  title="Reveal последнего раунда"
                  subtitle="Для Бредовухи сохраняется итоговый набор правды и лжи."
                  rows={game.bred_reveal_rows}
                />
                <ScoreRows rows={game.score_rows} title="Итоговое табло" subtitle="Последний зафиксированный счёт партии." />
              </article>
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
