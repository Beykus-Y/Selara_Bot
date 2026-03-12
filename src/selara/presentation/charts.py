from __future__ import annotations

from io import BytesIO
from typing import Sequence

from selara.domain.entities import LeaderboardItem, LeaderboardMode
from selara.domain.value_objects import display_name_from_parts
from selara.presentation.font_support import matplotlib_text_families


def _safe_import_matplotlib():
    try:
        import matplotlib

        matplotlib.use("Agg")
        matplotlib.rcParams["font.family"] = list(matplotlib_text_families())
        import matplotlib.pyplot as plt

        return plt
    except Exception:
        return None


_FIGURE_BG = "#07131f"
_PANEL_BG = "#0c1d31"
_TEXT_MAIN = "#f8fbff"
_TEXT_MUTED = "#9ab0c8"
_GRID = "#24415f"
_TRACK = "#132941"
_ACCENT_CYAN = "#67e8f9"
_ACCENT_BLUE = "#60a5fa"
_ACCENT_VIOLET = "#a78bfa"
_ACCENT_GOLD = "#fbbf24"
_ACCENT_ROSE = "#fb7185"
_ACCENT_MINT = "#5eead4"


def _style_chart(fig, ax, *, grid_axis: str) -> None:
    fig.patch.set_facecolor(_FIGURE_BG)
    ax.set_facecolor(_PANEL_BG)
    ax.grid(axis=grid_axis, alpha=0.34, linestyle="--", linewidth=0.85, color=_GRID)
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.tick_params(colors=_TEXT_MUTED, labelsize=10, length=0)
    ax.set_axisbelow(True)


def _add_header(fig, *, title: str, subtitle: str) -> None:
    fig.text(0.08, 0.94, title, color=_TEXT_MAIN, fontsize=16, fontweight="bold")
    fig.text(0.08, 0.9, subtitle, color=_TEXT_MUTED, fontsize=10.5)


def _add_chip(fig, *, x: float, y: float, label: str, value: str, edge: str) -> None:
    fig.text(
        x,
        y,
        f"{label}: {value}",
        color=_TEXT_MAIN,
        fontsize=10,
        fontweight="bold",
        bbox={
            "boxstyle": "round,pad=0.45,rounding_size=0.7",
            "facecolor": _PANEL_BG,
            "edgecolor": edge,
            "linewidth": 1.2,
            "alpha": 0.96,
        },
    )


def _safe_plot_max(values: Sequence[float]) -> float:
    if not values:
        return 1.0
    return max(max(values), 1.0)


def _truncate_label(value: str, *, limit: int = 24) -> str:
    if len(value) <= limit:
        return value
    return f"{value[:limit - 3]}..."


def build_profile_chart(*, activity_all: int, activity_7d: int, karma_all: int, karma_7d: int) -> bytes | None:
    plt = _safe_import_matplotlib()
    if plt is None:
        return None

    labels = ["Активность", "Карма"]
    all_values = [activity_all, karma_all]
    week_values = [activity_7d, karma_7d]
    max_value = _safe_plot_max([*all_values, *week_values])

    fig, ax = plt.subplots(figsize=(9.4, 5.6), dpi=160)
    _style_chart(fig, ax, grid_axis="y")
    fig.subplots_adjust(top=0.72, left=0.11, right=0.96, bottom=0.15)

    _add_header(
        fig,
        title="Профиль активности и кармы",
        subtitle="Сравнение долгого ритма с последними 7 днями.",
    )
    _add_chip(fig, x=0.08, y=0.82, label="Активность", value=f"{activity_7d} / {activity_all}", edge=_ACCENT_CYAN)
    _add_chip(fig, x=0.33, y=0.82, label="Карма", value=f"{karma_7d} / {karma_all}", edge=_ACCENT_VIOLET)

    x = [0, 1]
    width = 0.34
    ax.bar(x, [max_value, max_value], width=0.82, color=_TRACK, alpha=0.42, zorder=1)
    bars_all = ax.bar([value - width / 2 for value in x], all_values, width=width, label="За всё время", color=_ACCENT_BLUE, zorder=3)
    bars_week = ax.bar([value + width / 2 for value in x], week_values, width=width, label="За 7 дней", color=_ACCENT_MINT, zorder=4)

    ax.set_xticks(x)
    ax.set_xticklabels(labels, fontsize=11, color=_TEXT_MAIN)
    ax.set_ylim(0, max_value * 1.28 + 1)

    legend = ax.legend(frameon=False, loc="upper right", fontsize=10)
    for text in legend.get_texts():
        text.set_color(_TEXT_MAIN)

    ax.bar_label(bars_all, padding=4, fontsize=10, color=_TEXT_MAIN)
    ax.bar_label(bars_week, padding=4, fontsize=10, color=_TEXT_MAIN)

    buffer = BytesIO()
    fig.savefig(buffer, format="png")
    plt.close(fig)
    return buffer.getvalue()


def build_daily_activity_chart(*, points: Sequence[tuple[str, int]]) -> bytes | None:
    if not points:
        return None

    plt = _safe_import_matplotlib()
    if plt is None:
        return None

    labels = [label for label, _ in points]
    values = [value for _, value in points]
    x = list(range(len(labels)))

    fig, ax = plt.subplots(figsize=(10.4, 5.8), dpi=160)
    _style_chart(fig, ax, grid_axis="y")
    fig.subplots_adjust(top=0.72, left=0.08, right=0.97, bottom=0.18)

    total = sum(values)
    peak = max(values) if values else 0
    active_days = sum(1 for value in values if value > 0)
    average = (total / len(values)) if values else 0
    last_value = values[-1] if values else 0
    max_value = _safe_plot_max(values)

    _add_header(
        fig,
        title="Кто я: пульс активности",
        subtitle="Последние дни по сообщениям в этом чате.",
    )
    _add_chip(fig, x=0.08, y=0.82, label="Всего", value=str(total), edge=_ACCENT_CYAN)
    _add_chip(fig, x=0.23, y=0.82, label="Пик", value=str(peak), edge=_ACCENT_GOLD)
    _add_chip(fig, x=0.37, y=0.82, label="Среднее", value=f"{average:.1f}", edge=_ACCENT_VIOLET)
    _add_chip(fig, x=0.56, y=0.82, label="Активных дней", value=str(active_days), edge=_ACCENT_MINT)
    _add_chip(fig, x=0.79, y=0.82, label="Последний день", value=str(last_value), edge=_ACCENT_ROSE)

    colors = [
        _ACCENT_GOLD if value == peak and peak > 0 else (_ACCENT_VIOLET if index == len(values) - 1 else _ACCENT_BLUE)
        for index, value in enumerate(values)
    ]
    bars = ax.bar(x, values, color=colors, alpha=0.92, width=0.62, zorder=3)
    ax.plot(x, values, color=_ACCENT_CYAN, linewidth=2.4, marker="o", markersize=5.2, zorder=4)
    ax.fill_between(x, values, color=_ACCENT_CYAN, alpha=0.08, zorder=2)

    step = max(1, len(labels) // 7)
    tick_positions = x[::step]
    tick_labels = labels[::step]
    if tick_positions[-1] != x[-1]:
        tick_positions.append(x[-1])
        tick_labels.append(labels[-1])
    ax.set_xticks(tick_positions)
    ax.set_xticklabels(tick_labels, fontsize=10, color=_TEXT_MAIN)
    ax.set_ylabel("Сообщения", color=_TEXT_MUTED)
    ax.set_ylim(0, max_value * 1.28 + 1)

    for bar, value in zip(bars, values, strict=False):
        if value <= 0:
            continue
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            value + max_value * 0.04 + 0.15,
            str(value),
            ha="center",
            va="bottom",
            color=_TEXT_MAIN,
            fontsize=8.5,
            fontweight="bold",
        )

    if peak > 0 and len(values) > 1:
        peak_index = values.index(peak)
        ax.annotate(
            "пик",
            xy=(peak_index, peak),
            xytext=(peak_index, peak + max_value * 0.18 + 0.5),
            ha="center",
            color=_TEXT_MAIN,
            fontsize=9,
            fontweight="bold",
            arrowprops={"arrowstyle": "-", "color": _ACCENT_GOLD, "linewidth": 1.2},
            bbox={
                "boxstyle": "round,pad=0.28,rounding_size=0.5",
                "facecolor": _PANEL_BG,
                "edgecolor": _ACCENT_GOLD,
                "linewidth": 1.0,
                "alpha": 0.98,
            },
        )

    buffer = BytesIO()
    fig.savefig(buffer, format="png")
    plt.close(fig)
    return buffer.getvalue()


def build_leaderboard_chart(items: list[LeaderboardItem], *, mode: LeaderboardMode) -> bytes | None:
    if not items:
        return None

    plt = _safe_import_matplotlib()
    if plt is None:
        return None

    names = [
        _truncate_label(
            display_name_from_parts(
                user_id=item.user_id,
                username=item.username,
                first_name=item.first_name,
                last_name=item.last_name,
                chat_display_name=item.chat_display_name,
            ),
            limit=26,
        )
        for item in items
    ]

    if mode == "mix":
        values = [item.hybrid_score for item in items]
        title = "Топ пользователей: гибрид"
        subtitle = "Смешанный ритм кармы и активности."
        xlabel = "Рейтинг"
        base_color = _ACCENT_CYAN
    elif mode == "karma":
        values = [item.karma_value for item in items]
        title = "Топ пользователей: карма"
        subtitle = "Положение по выданной участникам карме."
        xlabel = "Карма"
        base_color = _ACCENT_ROSE
    else:
        values = [item.activity_value for item in items]
        title = "Топ пользователей: активность"
        subtitle = "Количество сообщений у участников в топе."
        xlabel = "Сообщения"
        base_color = _ACCENT_BLUE

    fig, ax = plt.subplots(figsize=(11.8, max(5.4, len(names) * 0.72 + 1.8)), dpi=160)
    _style_chart(fig, ax, grid_axis="x")
    fig.subplots_adjust(top=0.77, left=0.35, right=0.84, bottom=0.12)

    _add_header(fig, title=title, subtitle=subtitle)
    _add_chip(fig, x=0.08, y=0.83, label="Участников", value=str(len(items)), edge=base_color)
    _add_chip(fig, x=0.22, y=0.83, label="Лидер", value=names[0], edge=_ACCENT_GOLD)

    positions = list(range(len(names)))
    max_value = _safe_plot_max(values)
    has_negative = any(value < 0 for value in values)
    if not has_negative:
        ax.barh(positions, [max_value] * len(positions), color=_TRACK, height=0.64, alpha=0.6, zorder=1)

    palette = [_ACCENT_GOLD, _ACCENT_CYAN, _ACCENT_VIOLET] + [base_color] * max(0, len(values) - 3)
    bars = ax.barh(positions, values, color=palette[: len(values)], height=0.64, zorder=3)
    ax.set_yticks(positions)
    ax.set_yticklabels([])
    ax.invert_yaxis()
    ax.set_xlabel(xlabel, color=_TEXT_MUTED, labelpad=10)

    if has_negative:
        left_bound = min(values) * 1.18
        right_bound = max_value * 1.02
        if left_bound == right_bound:
            right_bound = left_bound + 1
        ax.set_xlim(left_bound, right_bound)
        ax.axvline(0, color=_GRID, linewidth=1.0, alpha=0.8, zorder=2)
    else:
        ax.set_xlim(0, max_value * 1.02)

    rank_column_x = 0.014
    name_column_x = -0.07
    value_column_x = 1.02

    for index, (bar, value) in enumerate(zip(bars, values, strict=False), start=1):
        y = bar.get_y() + bar.get_height() / 2
        ax.text(
            rank_column_x,
            y,
            f"{index:02d}",
            va="center",
            ha="left",
            color=_TEXT_MAIN,
            fontsize=8.5,
            fontweight="bold",
            transform=ax.get_yaxis_transform(),
            bbox={
                "boxstyle": "round,pad=0.3,rounding_size=0.5",
                "facecolor": _PANEL_BG,
                "edgecolor": palette[index - 1],
                "linewidth": 1.0,
                "alpha": 0.98,
            },
            zorder=4,
        )
        ax.text(
            name_column_x,
            y,
            names[index - 1],
            va="center",
            ha="right",
            color=_TEXT_MAIN,
            fontsize=10.5,
            transform=ax.get_yaxis_transform(),
            clip_on=False,
            zorder=4,
        )

        label = f"{value:.3f}" if mode == "mix" else str(value)
        ax.text(
            value_column_x,
            y,
            label,
            va="center",
            ha="left",
            color=_TEXT_MAIN,
            fontsize=9.2,
            fontweight="bold",
            transform=ax.get_yaxis_transform(),
            clip_on=False,
            zorder=4,
        )

    buffer = BytesIO()
    fig.savefig(buffer, format="png")
    plt.close(fig)
    return buffer.getvalue()
