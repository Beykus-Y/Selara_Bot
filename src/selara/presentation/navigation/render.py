"""Telegram HTML for feature cards and command lines on a catalog screen.

A feature card adds where a command works, who may use it, its limits and
where to go next on top of its CommandSpec. Without a card, a command still
gets a one-line entry so no catalog command silently drops off the screens.
Nothing here talks to Telegram or the database.
"""

from __future__ import annotations

from html import escape

from selara.presentation.commands.command_catalog import CommandSpec, get_command_spec
from selara.presentation.navigation.cards.model import FeatureCard
from selara.presentation.navigation.tree import get_nav_node

CONTEXT_LABELS: dict[str, str] = {
    "private": "в личке с ботом",
    "group": "в группе",
}

AUDIENCE_LABELS: dict[str, str] = {
    "all": "всем",
    "members": "участникам группы",
    "admins": "администраторам с правом Selara",
}


def _code(text: str) -> str:
    return f"<code>{escape(text)}</code>"


def _how(spec: CommandSpec) -> list[str]:
    """What the user types: slash syntax, else natural-language triggers, else examples.

    Natural-language features (for example clans or gacha) have no slash syntax at all.
    """
    if spec.syntax:
        return list(spec.syntax)
    if spec.natural_triggers:
        return [f"«{trigger}»" for trigger in spec.natural_triggers]
    return list(spec.examples)


def command_line(spec: CommandSpec) -> str:
    how = _how(spec)
    if not how:
        return f"• {escape(spec.title_ru)}"
    return f"• {_code(how[0])} — {escape(spec.title_ru)}"


def card_text(card: FeatureCard) -> str:
    spec = get_command_spec(card.spec_key)
    lines = [
        f"<b>{escape(spec.title_ru)}</b>",
        escape(spec.description_ru),
        "Где: " + ", ".join(CONTEXT_LABELS[context] for context in card.contexts),
    ]
    how = _how(spec)
    if how:
        lines.append("Как: " + ", ".join(_code(entry) for entry in how))
    lines.append("Кому: " + AUDIENCE_LABELS[card.audience])
    if card.limits:
        lines.append("Ограничения: " + "; ".join(escape(limit) for limit in card.limits))
    if card.related_nodes:
        lines.append("Дальше: " + ", ".join(escape(get_nav_node(key).title) for key in card.related_nodes))
    return "\n".join(lines)


def feature_block(spec_keys: tuple[str, ...], cards_by_spec: dict[str, FeatureCard]) -> str | None:
    """Cards where they exist, command lines for the rest; None when the node has no commands."""
    if not spec_keys:
        return None
    block = "<b>Функции</b>"
    previous_was_card = True
    for key in spec_keys:
        card = cards_by_spec.get(key)
        if card is not None:
            block += "\n\n" + card_text(card)
        else:
            block += ("\n\n" if previous_was_card else "\n") + command_line(get_command_spec(key))
        previous_was_card = card is not None
    return block
