import { Link } from 'react-router-dom'

import type {
  ChatDailyActivityPoint,
  ChatLeaderboardData,
  ChatLeaderboardMode,
  ChatOverviewData,
} from '@/pages/chat/model/types'
import { routes } from '@/shared/config/routes'

import './chat-page.css'

type ChatOverviewViewProps = {
  chatId: string
  activeTab: 'overview' | 'achievements' | 'settings'
  overview: ChatOverviewData
  leaderboard: ChatLeaderboardData
  searchValue: string
  onSearchValueChange: (value: string) => void
  onSearchSubmit: () => void
  onModeChange: (mode: ChatLeaderboardMode) => void
  onPageChange: (page: number) => void
  onFindMe: () => void
}

function activityBarHeight(point: ChatDailyActivityPoint, maxMessages: number) {
  if (maxMessages <= 0) {
    return 12
  }

  return Math.max(12, Math.round((point.messages / maxMessages) * 140))
}

function tabHref(chatId: string, tab: 'overview' | 'achievements' | 'settings') {
  return `/app/chat/${chatId}?tab=${tab}`
}

export function ChatOverviewView({
  chatId,
  activeTab,
  overview,
  leaderboard,
  searchValue,
  onSearchValueChange,
  onSearchSubmit,
  onModeChange,
  onPageChange,
  onFindMe,
}: ChatOverviewViewProps) {
  const maxMessages = Math.max(...overview.daily_activity.map((point) => point.messages), 0)
  const currentRankPage =
    leaderboard.my_rank !== null
      ? Math.max(1, Math.ceil(leaderboard.my_rank / leaderboard.page_size))
      : null

  return (
    <div className="chat-page">
      <section className="chat-hero">
        <div>
          <span className="page-card__eyebrow">Группа</span>
          <h1>{overview.chat_title}</h1>
          <p>{overview.hero_subtitle}</p>
        </div>
        <div className="chat-hero__chips">
          <span className="chat-chip">ID чата {chatId}</span>
          <span className="chat-chip">Живая статистика</span>
          <span className="chat-chip">{overview.can_manage_settings ? 'Есть доступ к управлению' : 'Просмотр без прав изменения'}</span>
        </div>
      </section>

      <section className="chat-tabs">
        <Link className={activeTab === 'overview' ? 'button button--primary' : 'button'} to={tabHref(chatId, 'overview')}>
          Обзор
        </Link>
        <Link className={activeTab === 'achievements' ? 'button button--primary' : 'button'} to={tabHref(chatId, 'achievements')}>
          Достижения
        </Link>
        {overview.can_manage_settings ? (
          <Link className={activeTab === 'settings' ? 'button button--primary' : 'button'} to={tabHref(chatId, 'settings')}>
            Настройки
          </Link>
        ) : null}
        <Link className="button" to={routes.economy(chatId)}>
          Экономика
        </Link>
        <Link className="button" to={routes.family(chatId)}>
          Моя семья
        </Link>
      </section>

      <section className="chat-metrics">
        {overview.metrics.map((metric) => (
          <article key={metric.label} className={`chat-metric chat-metric--${metric.tone}`}>
            <span className="chat-metric__label">{metric.label}</span>
            <strong className="chat-metric__value">{metric.value}</strong>
            <span className="chat-metric__note">{metric.note}</span>
          </article>
        ))}
      </section>

      <section className="chat-live-grid">
        <article className="chat-panel">
          <div className="chat-panel__head">
            <div>
              <h2>Активность по дням</h2>
              <p>Срез по последним семи дням.</p>
            </div>
          </div>
          <div className="chat-activity-chart">
            {overview.daily_activity.map((point) => (
              <div key={point.date} className="chat-activity-bar">
                <span className="chat-activity-bar__column" style={{ height: `${activityBarHeight(point, maxMessages)}px` }} />
                <strong>{point.messages}</strong>
                <small>{point.label}</small>
              </div>
            ))}
          </div>
        </article>

        <article className="chat-panel">
          <div className="chat-panel__head">
            <div>
              <h2>Герой дня</h2>
              <p>Самый активный участник за последние сутки.</p>
            </div>
          </div>
          <div className="chat-callout">
            {overview.hero_of_day ? (
              <>
                <strong>{overview.hero_of_day.label}</strong>
                <p>Сообщений: {overview.hero_of_day.messages}</p>
                <p>Карма: {overview.hero_of_day.karma}</p>
              </>
            ) : (
              <p>За последние 24 часа пока не набралось данных.</p>
            )}
          </div>
        </article>

        <article className="chat-panel">
          <div className="chat-panel__head">
            <div>
              <h2>Богач дня</h2>
              <p>Самый крупный баланс в активной экономике чата.</p>
            </div>
          </div>
          <div className="chat-callout">
            {overview.richest_of_day ? (
              <>
                <strong>{overview.richest_of_day.label}</strong>
                <p>Баланс: {overview.richest_of_day.balance}</p>
              </>
            ) : (
              <p>Экономика группы пока не дала лидера.</p>
            )}
          </div>
        </article>
      </section>

      <section className="chat-panel">
        <div className="chat-panel__head">
          <div>
            <h2>Полный лидерборд</h2>
            <p>Пагинация, поиск и переключение режима рейтинга по всей группе.</p>
          </div>
          <button className="button" type="button" onClick={onFindMe} disabled={leaderboard.my_rank === null || currentRankPage === leaderboard.page}>
            Найти меня
          </button>
        </div>

        <div className="chat-toolbar">
          <div className="chat-mode-switch" role="tablist" aria-label="Режим рейтинга">
            {(['mix', 'activity', 'karma'] as ChatLeaderboardMode[]).map((mode) => (
              <button
                key={mode}
                type="button"
                className={leaderboard.mode === mode ? 'chat-mode-switch__button chat-mode-switch__button--active' : 'chat-mode-switch__button'}
                onClick={() => onModeChange(mode)}
              >
                {mode === 'mix' ? 'Гибрид' : mode === 'activity' ? 'Сообщения' : 'Карма'}
              </button>
            ))}
          </div>

          <form
            className="chat-search"
            onSubmit={(event) => {
              event.preventDefault()
              onSearchSubmit()
            }}
          >
            <input
              type="search"
              name="q"
              placeholder="Имя, @username или Telegram ID"
              autoComplete="off"
              value={searchValue}
              onChange={(event) => onSearchValueChange(event.target.value)}
            />
            <button className="button" type="submit">
              Поиск
            </button>
          </form>
        </div>

        <p className="chat-status">
          Найдено строк: {leaderboard.total_rows}. Страница {leaderboard.page} из {leaderboard.total_pages}.
          {leaderboard.my_rank !== null ? ` Мой ранг: ${leaderboard.my_rank}.` : ''}
          {leaderboard.truncated ? ' Показаны только данные в пределах серверного лимита.' : ''}
        </p>

        <div className="chat-table-wrap">
          <table className="chat-table">
            <thead>
              <tr>
                <th>#</th>
                <th>Участник</th>
                <th className="chat-table__desktop">Username</th>
                <th>Сообщения</th>
                <th>Карма</th>
                <th>Рейтинг</th>
                <th className="chat-table__desktop">Активность</th>
              </tr>
            </thead>
            <tbody>
              {leaderboard.rows.map((row) => (
                <tr key={row.user_id} className={row.is_me ? 'chat-table__row chat-table__row--me' : 'chat-table__row'}>
                  <td>{row.position}</td>
                  <td>{row.name}</td>
                  <td className="chat-table__desktop">{row.username || '—'}</td>
                  <td>{row.activity}</td>
                  <td>{row.karma}</td>
                  <td>{row.hybrid_score}</td>
                  <td className="chat-table__desktop">{row.last_seen_at}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <div className="chat-pager">
          <button className="button" type="button" onClick={() => onPageChange(leaderboard.page - 1)} disabled={leaderboard.page <= 1}>
            Назад
          </button>
          <span className="chat-pager__info">
            {leaderboard.page}/{leaderboard.total_pages}
          </span>
          <button
            className="button"
            type="button"
            onClick={() => onPageChange(leaderboard.page + 1)}
            disabled={leaderboard.page >= leaderboard.total_pages}
          >
            Вперёд
          </button>
        </div>
      </section>

      <section className="chat-overview-grid">
        {overview.dashboard_panels.map((panel) => (
          <article key={panel.title} className="chat-panel">
            <div className="chat-panel__head">
              <div>
                <h2>{panel.title}</h2>
              </div>
            </div>
            {panel.empty_text ? (
              <p className="chat-status">{panel.empty_text}</p>
            ) : (
              <div className="chat-stat-list">
                {panel.rows.map((row) => (
                  <div key={`${panel.title}-${row.title}`} className="chat-stat-row">
                    <div>
                      <strong>{row.title}</strong>
                      <p>{row.meta}</p>
                    </div>
                    <span>{row.value}</span>
                  </div>
                ))}
              </div>
            )}
          </article>
        ))}
      </section>

      <section className="chat-overview-grid">
        <article className="chat-panel">
          <div className="chat-panel__head">
            <div>
              <h2>Ваш доступ</h2>
              <p>Текущая роль, права и режим экономики в группе.</p>
            </div>
          </div>
          <div className="chat-stat-list">
            {overview.access_rows.map((row) => (
              <div key={row.title} className="chat-stat-row">
                <div>
                  <strong>{row.title}</strong>
                  <p>{row.meta}</p>
                </div>
                <span>{row.value}</span>
              </div>
            ))}
          </div>
        </article>

        <article className="chat-panel">
          <div className="chat-panel__head">
            <div>
              <h2>Роли и ранги команд</h2>
              <p>Настроенные роли бота и ограничения на команды.</p>
            </div>
          </div>

          <div className="chat-role-list">
            {overview.roles.map((role) => (
              <div key={role.code} className="chat-stat-row">
                <div>
                  <strong>{role.title}</strong>
                  <p>{role.meta}</p>
                </div>
                <span className="chat-role-permissions">{role.permissions}</span>
              </div>
            ))}
          </div>

          {overview.command_rules.length > 0 ? (
            <div className="chat-stat-list">
              {overview.command_rules.map((rule) => (
                <div key={rule.command} className="chat-stat-row">
                  <div>
                    <strong>{rule.command}</strong>
                    <p>Минимальная роль для команды</p>
                  </div>
                  <span>{rule.role}</span>
                </div>
              ))}
            </div>
          ) : (
            <p className="chat-status">Индивидуальные ограничения команд пока не заданы.</p>
          )}
        </article>
      </section>

      <section className="chat-leaderboard-grid">
        {overview.leaderboards.map((board) => (
          <article key={board.title} className={`chat-panel chat-panel--accent-${board.accent}`}>
            <div className="chat-panel__head">
              <div>
                <h2>{board.title}</h2>
                <p>{board.subtitle}</p>
              </div>
            </div>

            {board.rows.length > 0 ? (
              <div className="chat-rank-list">
                {board.rows.map((row) => (
                  <div key={`${board.title}-${row.position}-${row.name}`} className="chat-rank-row">
                    <span className="chat-rank-row__position">{row.position}</span>
                    <div>
                      <strong>{row.name}</strong>
                      <p>{row.secondary}</p>
                    </div>
                    <span>{row.primary}</span>
                  </div>
                ))}
              </div>
            ) : (
              <p className="chat-status">Для этого рейтинга пока не хватает данных.</p>
            )}
          </article>
        ))}
      </section>

      <section className="chat-panel">
        <div className="chat-panel__head">
          <div>
            <h2>Свежие действия</h2>
            <p>Короткий срез последних событий бота и админов в группе.</p>
          </div>
          <Link className="button" to={routes.audit(chatId)}>
            Открыть журнал
          </Link>
        </div>

        {overview.audit_rows.length > 0 ? (
          <div className="chat-audit-list">
            {overview.audit_rows.map((row, index) => (
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
