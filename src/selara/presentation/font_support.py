from __future__ import annotations


_GENERIC_FAMILY_PARAMS = {
    "sans-serif": "font.sans-serif",
    "serif": "font.serif",
    "cursive": "font.cursive",
    "fantasy": "font.fantasy",
    "monospace": "font.monospace",
}
_EMOJI_FONT_FAMILIES = (
    "Noto Color Emoji",
    "Segoe UI Emoji",
    "Apple Color Emoji",
    "Noto Emoji",
    "Twemoji Mozilla",
    "EmojiOne Color",
    "Symbola",
)


def _dedupe(values: list[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return tuple(result)


def _normalize_family_values(value) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)):
        return [item for item in value if isinstance(item, str) and item]
    return []


def matplotlib_base_families() -> tuple[str, ...]:
    try:
        from matplotlib import rcParams
    except Exception:
        return ("DejaVu Sans",)

    configured = _normalize_family_values(rcParams.get("font.family")) or ["sans-serif"]
    families: list[str] = []
    for family in configured:
        rc_param = _GENERIC_FAMILY_PARAMS.get(family)
        if rc_param is None:
            families.append(family)
            continue
        expanded = _normalize_family_values(rcParams.get(rc_param))
        families.extend(expanded or [family])
    return _dedupe(families or ["DejaVu Sans"])


def matplotlib_text_families() -> tuple[str, ...]:
    return _dedupe([*matplotlib_base_families(), *_EMOJI_FONT_FAMILIES])


def resolve_matplotlib_font_path(*, bold: bool) -> str | None:
    try:
        from matplotlib import font_manager
    except Exception:
        return None

    try:
        resolved = font_manager.findfont(
            font_manager.FontProperties(
                family=list(matplotlib_base_families()),
                weight="bold" if bold else "normal",
            ),
            fallback_to_default=True,
        )
    except Exception:
        return None
    return resolved or None


def resolve_emoji_font_paths() -> tuple[str, ...]:
    try:
        from matplotlib import font_manager
    except Exception:
        return ()

    candidates: list[str] = []
    for family in _EMOJI_FONT_FAMILIES:
        try:
            resolved = font_manager.findfont(
                font_manager.FontProperties(family=[family]),
                fallback_to_default=False,
            )
        except Exception:
            continue
        if resolved:
            candidates.append(resolved)
    return _dedupe(candidates)
