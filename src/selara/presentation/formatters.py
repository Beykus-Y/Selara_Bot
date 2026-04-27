from datetime import datetime, timedelta, timezone
from html import escape

from selara.application.dto import RepStats
from selara.core.timezone import to_timezone
from selara.domain.entities import ActivityStats, LeaderboardItem, LeaderboardMode, LeaderboardPeriod
from selara.domain.value_objects import display_name_from_parts


def format_user_link(*, user_id: int, label: str) -> str:
    return f'<a href="tg://user?id={user_id}">{escape(label)}</a>'


def preferred_mention_label_from_parts(
    *,
    user_id: int,
    username: str | None,
    first_name: str | None,
    last_name: str | None,
    chat_display_name: str | None = None,
) -> str:
    return display_name_from_parts(
        user_id=user_id,
        username=username,
        first_name=first_name,
        last_name=last_name,
        chat_display_name=chat_display_name,
    )


def format_user_mention_html(
    *,
    user_id: int,
    username: str | None,
    first_name: str | None,
    last_name: str | None,
    chat_display_name: str | None = None,
) -> str:
    return format_user_link(
        user_id=user_id,
        label=preferred_mention_label_from_parts(
            user_id=user_id,
            username=username,
            first_name=first_name,
            last_name=last_name,
            chat_display_name=chat_display_name,
        ),
    )


def _format_relative(localized_value: datetime) -> str:
    now = datetime.now(localized_value.tzinfo or timezone.utc)
    delta = now - localized_value
    seconds = max(int(delta.total_seconds()), 0)

    if seconds < 60:
        return "только что"
    if seconds < 3600:
        return f"{seconds // 60} мин назад"
    if seconds < 86400:
        return f"{seconds // 3600} ч назад"
    return f"{seconds // 86400} дн назад"


def _format_dt(value: datetime, timezone_name: str) -> str:
    localized = to_timezone(value, timezone_name)
    now = datetime.now(localized.tzinfo or timezone.utc)

    if localized.date() == now.date():
        base = f"сегодня в {localized.strftime('%H:%M')}"
    elif localized.date() == (now.date() - timedelta(days=1)):
        base = f"вчера в {localized.strftime('%H:%M')}"
    else:
        base = localized.strftime("%d.%m.%Y в %H:%M")

    tz_title = localized.strftime("%Z")
    relative = _format_relative(localized)
    return f"{base} ({tz_title}), {relative}"


def format_elapsed_compact(value: datetime, timezone_name: str) -> str:
    localized = to_timezone(value, timezone_name)
    now = datetime.now(localized.tzinfo or timezone.utc)
    total_seconds = max(int((now - localized).total_seconds()), 0)

    if total_seconds < 60:
        return "только что"

    days, rem = divmod(total_seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    months, days = divmod(days, 30)

    if months:
        return f"{months} мес {days} дн назад" if days else f"{months} мес назад"
    if days:
        return f"{days} дн {hours} ч назад" if hours else f"{days} дн назад"
    if hours:
        return f"{hours} ч {minutes} мин назад" if minutes else f"{hours} ч назад"
    return f"{minutes} мин назад"


def _format_first_seen(value: datetime, timezone_name: str) -> str:
    localized = to_timezone(value, timezone_name)
    return f"{localized.strftime('%d.%m.%Y')} ({format_elapsed_compact(value, timezone_name)})"


def format_activity_pulse_line(*, day: int, week: int, month: int, all_time: int, iris_view: bool = False) -> str:
    if iris_view:
        return f"{day} | {week} | {month} | {all_time}"
    return f"1д {day} • 7д {week} • 30д {month} • всё {all_time}"


def format_me(
    stats: ActivityStats | None,
    *,
    timezone_name: str,
    fallback_user_id: int,
    activity_pulse: str | None = None,
    activity_pulse_label: str = "Вся активность",
    user_label_html: str | None = None,
) -> str:
    if stats is None:
        user_html = user_label_html or format_user_link(user_id=fallback_user_id, label=f"user:{fallback_user_id}")
        text = (
            f"<b>Пользователь:</b> {user_html}\n"
            "<b>Последний актив:</b> нет данных"
        )
        if activity_pulse:
            text += f"\n<b>{escape(activity_pulse_label)}:</b> {activity_pulse}"
        return text

    user_html = user_label_html or format_user_mention_html(
        user_id=stats.user_id,
        username=stats.username,
        first_name=stats.first_name,
        last_name=stats.last_name,
        chat_display_name=stats.chat_display_name,
    )
    text = (
        f"<b>Пользователь:</b> {user_html}\n"
        + (
            f"<b>Первое появление:</b> {_format_first_seen(stats.first_seen_at, timezone_name)}\n"
            if stats.first_seen_at is not None
            else ""
        )
        + f"<b>Последний актив:</b> {_format_dt(stats.last_seen_at, timezone_name)}"
    )
    if activity_pulse:
        text += f"\n<b>{escape(activity_pulse_label)}:</b> {activity_pulse}"
    return text


def format_profile_positions_line(*, rank_all: int | None, rank_7d: int | None) -> str:
    all_value = f"#{rank_all}" if rank_all is not None else "-"
    seven_value = f"#{rank_7d}" if rank_7d is not None else "-"
    return f"<b>Позиция:</b> всё <code>{all_value}</code> • 7д <code>{seven_value}</code>"


def format_profile_karma_line(*, karma_all: int, karma_7d: int) -> str:
    return f"<b>Карма:</b> всё <code>{karma_all}</code> • 7д <code>{karma_7d}</code>"


def format_top(stats_list: list[ActivityStats], *, timezone_name: str, limit: int) -> str:
    if not stats_list:
        return "<b>Пока нет данных об активности в этом чате.</b>"

    lines = [f"<b>Топ пользователей за всё время</b>\n<b>Лимит:</b> <code>{limit}</code>"]
    for index, item in enumerate(stats_list, start=1):
        user_html = format_user_mention_html(
            user_id=item.user_id,
            username=item.username,
            first_name=item.first_name,
            last_name=item.last_name,
            chat_display_name=item.chat_display_name,
        )
        lines.append(
            f"<b>{index}.</b> {user_html} — <code>{item.message_count}</code> сообщ. | "
            f"<code>{_format_dt(item.last_seen_at, timezone_name)}</code>"
        )
    return "\n".join(lines)


def format_last_seen(*, user_label: str, last_seen_at: datetime | None, timezone_name: str) -> str:
    if last_seen_at is None:
        return f"<b>{escape(user_label)}:</b> нет данных об активности"

    return f"<b>{escape(user_label)}:</b> {_format_dt(last_seen_at, timezone_name)}"


def _activity_label(period: LeaderboardPeriod) -> str:
    return "сообщений всего" if period == "all" else "сообщений за период"


def _karma_label(period: LeaderboardPeriod) -> str:
    return "карма за всё время" if period == "all" else "карма за период"


def _period_title(period: LeaderboardPeriod) -> str:
    return {
        "all": "за всё время",
        "7d": "за 7 дней",
        "hour": "за последний час",
        "day": "за последние сутки",
        "week": "за текущую неделю",
        "month": "за последние 30 дней",
    }[period]


def _format_leaderboard_user_html(item: LeaderboardItem) -> str:
    return format_user_mention_html(
        user_id=item.user_id,
        username=item.username,
        first_name=item.first_name,
        last_name=item.last_name,
        chat_display_name=item.chat_display_name,
    )


def format_leaderboard(
    items: list[LeaderboardItem],
    *,
    mode: LeaderboardMode,
    period: LeaderboardPeriod,
    limit: int,
    timezone_name: str,
    activity_less_than: int | None = None,
) -> str:
    if not items:
        if mode == "activity" and activity_less_than is not None:
            return f"<b>Нет пользователей с активностью меньше <code>{activity_less_than}</code> для выбранного периода.</b>"
        return "<b>Пока нет данных для выбранного рейтинга.</b>"

    if mode == "activity":
        title = _period_title(period)
        if activity_less_than is None:
            lines = [f"<b>Топ пользователей {title}</b>\n<b>Лимит:</b> <code>{limit}</code>"]
        else:
            lines = [
                f"<b>Пользователи {title} с активностью меньше <code>{activity_less_than}</code></b>\n"
                f"<b>Лимит:</b> <code>{limit}</code>"
            ]
        for index, item in enumerate(items, start=1):
            user_html = _format_leaderboard_user_html(item)
            lines.append(f"<b>{index}.</b> {user_html} — <code>{item.activity_value}</code> сообщ.")
        return "\n".join(lines)

    mode_title = {"mix": "гибрид", "activity": "активность", "karma": "карма"}[mode]
    period_title = _period_title(period)
    lines = [f"<b>Топ пользователей</b>\n<b>Режим:</b> {mode_title} | <b>Период:</b> {period_title} | <b>Лимит:</b> {limit}"]

    for index, item in enumerate(items, start=1):
        user_html = _format_leaderboard_user_html(item)
        item_lines = [f"<b>{index}.</b> {user_html}"]

        if mode == "mix":
            item_lines.append(f"<code>гибридный балл: {item.hybrid_score:.3f}</code>")
            item_lines.append(
                f"<code>{_activity_label(period)}: {item.activity_value} | {_karma_label(period)}: {item.karma_value}</code>"
            )
        elif mode == "karma":
            item_lines.append(f"<code>{_karma_label(period)}: {item.karma_value}</code>")
            item_lines.append(f"<code>{_activity_label(period)}: {item.activity_value}</code>")
        else:
            last_seen_text = _format_dt(item.last_seen_at, timezone_name) if item.last_seen_at is not None else "нет данных"
            item_lines = [
                f"<b>{index}.</b> {escape(name)} — <code>{item.activity_value}</code>",
                f"<code>последнее сообщение: {last_seen_text}</code>",
            ]

        lines.append("\n".join(item_lines))

    return "\n".join(lines)


def format_rep_stats(stats: RepStats, *, user_label: str | None = None, user_label_html: str | None = None) -> str:
    profile_label = user_label_html or escape(user_label or "пользователь")
    pulse_line = format_activity_pulse_line(
        day=stats.activity_1d,
        week=stats.activity_7d,
        month=stats.activity_30d,
        all_time=stats.activity_all,
    )
    lines = [
        f"<b>Профиль:</b> {profile_label}",
        f"<b>Карма за всё время:</b> <code>{stats.karma_all}</code>",
        f"<b>Карма за 7 дней:</b> <code>{stats.karma_7d}</code>",
        f"<b>Активность за 1 день:</b> <code>{stats.activity_1d}</code>",
        f"<b>Активность за всё время:</b> <code>{stats.activity_all}</code>",
        f"<b>Активность за 7 дней:</b> <code>{stats.activity_7d}</code>",
        f"<b>Активность за 30 дней:</b> <code>{stats.activity_30d}</code>",
        f"<b>Пульс активности:</b> {pulse_line}",
        f"<b>Позиция в гибриде (всё время):</b> <code>{stats.rank_all if stats.rank_all is not None else '-'}</code>",
        f"<b>Позиция в гибриде (7 дней):</b> <code>{stats.rank_7d if stats.rank_7d is not None else '-'}</code>",
    ]
    return "\n".join(lines)
