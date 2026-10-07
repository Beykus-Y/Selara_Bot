"""Which model a Personal AI turn uses and what it costs in AI Limits (AIL).

The user picks a logical profile (``basic``, ``analytics``, ...), never a physical model id.
Every turn resolves that choice once, against one catalog snapshot: the resulting
``ResolvedModel`` is both the model sent to the provider and the source of the AIL multiplier
charged for the turn, so an admin edit in between cannot split them.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from selara.application.feature_access import validate_ail_units
from selara.application.model_catalog import (
    PROFILE_DESCRIPTIONS,
    PROFILE_EMOJI,
    PROFILE_NAMES,
    PROFILE_ORDER,
    CatalogProvider,
    CatalogSnapshot,
)
from selara.application.model_router import ResolvedModel, resolve_from_snapshot

BASIC_PROFILE = "basic"
# A personal chat turn is plain text: no tools, no structured output, no vision.
LEGACY_AIL_MULTIPLIER = Decimal("1")


def is_profile_key(value: object) -> bool:
    return isinstance(value, str) and value in PROFILE_NAMES


def profile_display_name(snapshot: CatalogSnapshot | None, profile_key: str) -> str:
    profile = snapshot.profiles_by_key.get(profile_key) if snapshot is not None else None
    return profile.display_name if profile is not None else PROFILE_NAMES.get(profile_key, profile_key)


def _valid_multiplier(value: Decimal) -> bool:
    try:
        validate_ail_units(value)
    except ValueError:
        return False
    return True


def is_usable(resolved: ResolvedModel) -> bool:
    """A profile users may run: enabled, assigned to an enabled compatible model, with a storable multiplier."""
    return not resolved.is_fallback and resolved.capabilities is not None and _valid_multiplier(resolved.ail_multiplier)


@dataclass(frozen=True, slots=True)
class PersonalModelChoice:
    """The single resolution of one Personal AI turn."""

    selected_key: str
    effective: ResolvedModel
    display_name: str
    # The selected profile could not be used, so the turn runs (and is charged) as Базовая.
    fell_back: bool

    @property
    def profile_key(self) -> str:
        return self.effective.profile_key or BASIC_PROFILE

    @property
    def ail_cost(self) -> Decimal:
        return self.effective.ail_multiplier


def _basic_or_legacy(snapshot: CatalogSnapshot | None, legacy_model: str) -> ResolvedModel:
    basic = resolve_from_snapshot(snapshot, profile_key=BASIC_PROFILE, legacy_model=legacy_model)
    if is_usable(basic):
        return basic
    # The legacy default model is the last resort; it costs the baseline 1 AIL.
    return ResolvedModel(
        model_id=legacy_model, profile_key=BASIC_PROFILE, ail_multiplier=LEGACY_AIL_MULTIPLIER, catalog=snapshot
    )


def choose_from_snapshot(snapshot: CatalogSnapshot | None, *, selected_key: str | None, legacy_model: str) -> PersonalModelChoice:
    key = selected_key if is_profile_key(selected_key) else BASIC_PROFILE
    if key != BASIC_PROFILE:
        resolved = resolve_from_snapshot(snapshot, profile_key=key, legacy_model=legacy_model)
        if is_usable(resolved):
            return PersonalModelChoice(key, resolved, profile_display_name(snapshot, key), fell_back=False)
    effective = _basic_or_legacy(snapshot, legacy_model)
    return PersonalModelChoice(
        key, effective, profile_display_name(snapshot, BASIC_PROFILE), fell_back=key != BASIC_PROFILE
    )


async def load_snapshot(catalog: CatalogProvider | None) -> CatalogSnapshot | None:
    if catalog is None:
        return None
    try:
        return await catalog.get()
    except Exception:
        # The cached provider already keeps last-known-good; anything else routes to legacy.
        return None


async def resolve_personal_model(
    catalog: CatalogProvider | None, *, selected_key: str | None, legacy_model: str
) -> PersonalModelChoice:
    """Resolve the user's profile exactly once for a turn (AIL mode)."""
    return choose_from_snapshot(await load_snapshot(catalog), selected_key=selected_key, legacy_model=legacy_model)


@dataclass(frozen=True, slots=True)
class ProfileOption:
    profile_key: str
    emoji: str
    display_name: str
    description: str
    ail_multiplier: Decimal
    available: bool


def profile_options(snapshot: CatalogSnapshot | None, *, legacy_model: str) -> list[ProfileOption]:
    """What the selector shows: Базовая always works (legacy fallback), others only when usable."""
    options: list[ProfileOption] = []
    for key in PROFILE_ORDER:
        resolved = (
            _basic_or_legacy(snapshot, legacy_model)
            if key == BASIC_PROFILE
            else resolve_from_snapshot(snapshot, profile_key=key, legacy_model=legacy_model)
        )
        options.append(
            ProfileOption(
                profile_key=key,
                emoji=PROFILE_EMOJI[key],
                display_name=profile_display_name(snapshot, key),
                description=PROFILE_DESCRIPTIONS[key],
                ail_multiplier=resolved.ail_multiplier,
                available=key == BASIC_PROFILE or is_usable(resolved),
            )
        )
    return options


def ail_activation_problems(snapshot: CatalogSnapshot | None) -> list[str]:
    """Why AI Limits cannot be switched on with this catalog (empty: it can)."""
    problems: list[str] = []
    if snapshot is None:
        return ["Каталог моделей недоступен."]
    basic = resolve_from_snapshot(snapshot, profile_key=BASIC_PROFILE, legacy_model="legacy")
    if not is_usable(basic):
        problems.append("Профиль «basic» должен быть включён и назначен на включённую модель.")
    for profile in snapshot.profiles:
        if profile.enabled and not _valid_multiplier(profile.ail_multiplier):
            problems.append(
                f"Профиль «{profile.profile_key}»: AIL multiplier должен быть больше 0, до 1000 и не точнее 0.01."
            )
    return problems


def format_ail(value: Decimal | int | float | None) -> str:
    """2.5 → «2.5», 5.00 → «5»; never a float artefact."""
    if value is None:
        return "—"
    number = Decimal(str(value)).quantize(Decimal("0.01"))
    text = format(number.normalize(), "f")
    return text
