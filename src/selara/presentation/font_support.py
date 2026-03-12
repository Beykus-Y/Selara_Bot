from __future__ import annotations


_GENERIC_FAMILY_PARAMS = {
    "sans-serif": "font.sans-serif",
    "serif": "font.serif",
    "cursive": "font.cursive",
    "fantasy": "font.fantasy",
    "monospace": "font.monospace",
}
_PIL_EMOJI_FONT_FAMILIES = (
    "Noto Color Emoji",
    "Segoe UI Emoji",
    "Apple Color Emoji",
    "Noto Emoji",
    "Symbola",
    "Twemoji Mozilla",
    "EmojiOne Color",
)
_MATPLOTLIB_EMOJI_FONT_FAMILIES = (
    "Twemoji Mozilla",
    "EmojiOne Color",
    "Symbola",
    "Noto Emoji",
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


def _configured_matplotlib_families() -> tuple[str, ...]:
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


def _installed_matplotlib_families() -> set[str]:
    try:
        from matplotlib import font_manager
    except Exception:
        return set()
    return {entry.name for entry in font_manager.fontManager.ttflist}


def resolve_matplotlib_font_path(*, bold: bool) -> str | None:
    try:
        from matplotlib import font_manager
    except Exception:
        return None

    try:
        resolved = font_manager.findfont(
            font_manager.FontProperties(
                family=list(_configured_matplotlib_families()),
                weight="bold" if bold else "normal",
            ),
            fallback_to_default=True,
        )
    except Exception:
        return None
    return resolved or None


def matplotlib_base_families() -> tuple[str, ...]:
    try:
        from matplotlib import font_manager
    except Exception:
        return ("DejaVu Sans",)

    resolved = resolve_matplotlib_font_path(bold=False)
    if resolved:
        try:
            family = font_manager.FontProperties(fname=resolved).get_name()
        except Exception:
            family = None
        if family:
            return (family,)

    installed = _installed_matplotlib_families()
    for family in _configured_matplotlib_families():
        if family in installed:
            return (family,)
    return ("DejaVu Sans",)


def matplotlib_text_families() -> tuple[str, ...]:
    installed = _installed_matplotlib_families()
    emoji_families = [family for family in _MATPLOTLIB_EMOJI_FONT_FAMILIES if family in installed]
    return _dedupe([*matplotlib_base_families(), *emoji_families])


def resolve_emoji_font_paths() -> tuple[str, ...]:
    try:
        from matplotlib import font_manager
    except Exception:
        return ()

    candidates: list[str] = []
    for family in _PIL_EMOJI_FONT_FAMILIES:
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
