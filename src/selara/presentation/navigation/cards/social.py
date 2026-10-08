"""Feature cards for the 'social' navigation area: couples and family, clans,
chat naming, reputation and quote cards, personas.

Limits are written in words, not numbers: the real values live in Settings and
resolve_feature_policy, and a number copied here would drift.
"""

from __future__ import annotations

from selara.presentation.navigation.cards.model import FeatureCard

CARDS: tuple[FeatureCard, ...] = (
    FeatureCard(
        spec_key="relationships_start",
        contexts=("group",),
        audience="members",
        limits=(
            "Предложение пары или брака отправляется в группе: reply или @username.",
            "Ответ подтверждается кнопками, а предложение истекает, если его не приняли вовремя.",
        ),
    ),
    FeatureCard(
        spec_key="relationships_pair_actions",
        contexts=("group",),
        audience="members",
        limits=(
            "/flirt и /surprise работают только на стадии пары.",
            "У действий есть кулдаун; текущий остаток показывает /relation.",
        ),
    ),
    FeatureCard(
        spec_key="relationships_marriage_actions",
        contexts=("group",),
        audience="members",
        limits=(
            "/love и /vow доступны только в браке.",
            "Действие с reply не на своего партнёра отклоняется.",
        ),
    ),
    FeatureCard(
        spec_key="relationships_end",
        contexts=("group",),
        audience="members",
        limits=(
            "Пара и брак закрываются разными командами.",
            "После развода супруг убирается из семейного графа.",
        ),
    ),
    FeatureCard(
        spec_key="family_adopt_pet_tree",
        contexts=("group",),
        audience="members",
        limits=(
            "Создание связи подтверждается кнопками согласия.",
            "/adopt и /adoptdaughter различаются только ролью: «сын» или «дочь».",
        ),
        related_nodes=("pets",),
    ),
    FeatureCard(
        spec_key="family_escape",
        contexts=("group",),
        audience="members",
        limits=(
            "/escapefamily — только если у вас есть родитель в этом чате.",
            "/escapepet — только если вы чей-то питомец в этом чате.",
            "Действие подтверждается кнопкой и необратимо.",
        ),
    ),
    FeatureCard(
        spec_key="clans_core",
        contexts=("group",),
        audience="members",
        limits=(
            "Кланы привязаны к чату: список, вступление и поиск по названию работают только в нём.",
            "Создатель не может выйти из клана, он может только удалить его.",
        ),
    ),
    FeatureCard(
        spec_key="social_naming",
        contexts=("group",),
        audience="members",
        limits=(
            "Меняет обращение только в этом чате.",
            "Сброс — словами «сброс» или «удалить».",
        ),
    ),
    FeatureCard(
        spec_key="social_karma_reply",
        contexts=("group",),
        audience="members",
        limits=("Слэш-команды нет: работает только ответом на сообщение участника.",),
    ),
    FeatureCard(
        spec_key="social_quote_card",
        contexts=("private", "group"),
        audience="all",
        limits=("Нужен reply на сообщение, которое нужно процитировать.",),
    ),
    FeatureCard(
        spec_key="social_personas",
        contexts=("group",),
        audience="admins",
        limits=(
            "Работает, только если в чате включена настройка образов; иначе бот сообщит об этом.",
            "Образ подменяет только отображаемое имя в ответах бота.",
        ),
        related_nodes=("admin_settings",),
    ),
)
