from __future__ import annotations

from io import BytesIO
import unicodedata

from selara.presentation.charts import (
    _ACCENT_CYAN,
    _ACCENT_GOLD,
    _ACCENT_ROSE,
    _ACCENT_VIOLET,
    _FIGURE_BG,
    _GRID,
    _PANEL_BG,
    _TEXT_MAIN,
    _TEXT_MUTED,
)


_CARD_WIDTH = 196
_CARD_RADIUS = 24
_CARD_OUTLINE_WIDTH = 2
_LINE_WIDTH = 4
_DASHED_WIDTH = 3
_EMOJI_FONT_FAMILIES = (
    "Noto Color Emoji",
    "Segoe UI Emoji",
    "Apple Color Emoji",
    "Noto Emoji",
    "Twemoji Mozilla",
    "EmojiOne Color",
    "Symbola",
)
_EMOJI_FIXED_SIZES = (109, 128, 96, 72, 64)
_EMOJI_RANGES = (
    (0x1F1E6, 0x1F1FF),
    (0x1F300, 0x1F5FF),
    (0x1F600, 0x1F64F),
    (0x1F680, 0x1F6FF),
    (0x1F700, 0x1F77F),
    (0x1F780, 0x1F7FF),
    (0x1F800, 0x1F8FF),
    (0x1F900, 0x1F9FF),
    (0x1FA70, 0x1FAFF),
    (0x2600, 0x26FF),
    (0x2700, 0x27BF),
)


def _font_candidates(*, bold: bool) -> tuple[str, ...]:
    try:
        from matplotlib import font_manager, rcParams

        families = rcParams.get("font.family") or ["sans-serif"]
        if not isinstance(families, list):
            families = [families]
        resolved = font_manager.findfont(
            font_manager.FontProperties(family=families, weight="bold" if bold else "normal"),
            fallback_to_default=True,
        )
        if resolved:
            return (resolved,)
    except Exception:
        pass
    return ()


def _emoji_font_candidates() -> tuple[str, ...]:
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
        if resolved and resolved not in candidates:
            candidates.append(resolved)
    return tuple(candidates)


def _load_font(size: int, *, bold: bool = False):
    from PIL import ImageFont

    for candidate in _font_candidates(bold=bold):
        try:
            return ImageFont.truetype(candidate, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def _load_emoji_font(size: int):
    from PIL import ImageFont

    load_sizes = (size, *[candidate for candidate in _EMOJI_FIXED_SIZES if candidate != size])
    for candidate in _emoji_font_candidates():
        for load_size in load_sizes:
            try:
                return ImageFont.truetype(candidate, size=load_size), load_size
            except OSError:
                continue
    return None


def _is_emoji_char(value: str) -> bool:
    if not value:
        return False
    codepoint = ord(value)
    return any(start <= codepoint <= end for start, end in _EMOJI_RANGES)


def _is_regional_indicator(value: str) -> bool:
    return bool(value) and 0x1F1E6 <= ord(value) <= 0x1F1FF


def _is_emoji_modifier(value: str) -> bool:
    return bool(value) and 0x1F3FB <= ord(value) <= 0x1F3FF


def _is_variation_selector(value: str) -> bool:
    return value in {"\ufe0e", "\ufe0f"}


def _is_keycap_base(value: str) -> bool:
    return value in set("0123456789#*")


def _is_emoji_start(text: str, index: int) -> bool:
    value = text[index]
    if _is_emoji_char(value) or _is_regional_indicator(value):
        return True
    if _is_keycap_base(value):
        next_value = text[index + 1] if index + 1 < len(text) else ""
        after_next = text[index + 2] if index + 2 < len(text) else ""
        return next_value == "\ufe0f" or next_value == "\u20e3" or after_next == "\u20e3"
    return False


def _consume_emoji_cluster(text: str, start: int) -> tuple[str, int]:
    cluster = [text[start]]
    index = start + 1

    if _is_regional_indicator(cluster[0]) and index < len(text) and _is_regional_indicator(text[index]):
        cluster.append(text[index])
        index += 1

    while index < len(text):
        value = text[index]
        if _is_variation_selector(value) or value == "\u20e3" or _is_emoji_modifier(value) or unicodedata.combining(value):
            cluster.append(value)
            index += 1
            continue
        if value == "\u200d" and index + 1 < len(text):
            cluster.append(value)
            cluster.append(text[index + 1])
            index += 2
            continue
        break
    return "".join(cluster), index


def _split_text_runs(text: str) -> list[tuple[str, bool]]:
    runs: list[tuple[str, bool]] = []
    index = 0
    while index < len(text):
        if _is_emoji_start(text, index):
            cluster, index = _consume_emoji_cluster(text, index)
            runs.append((cluster, True))
            continue
        start = index
        index += 1
        while index < len(text) and not _is_emoji_start(text, index):
            index += 1
        runs.append((text[start:index], False))
    return runs


def _wrap_lines(text: str, *, line_len: int = 20) -> list[str]:
    normalized = " ".join((text or "").split()).strip()
    if not normalized:
        return ["-"]
    words = normalized.split(" ")
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = word if not current else f"{current} {word}"
        if len(candidate) <= line_len:
            current = candidate
            continue
        if current:
            lines.append(current)
        current = word
    if current:
        lines.append(current)
    return lines[:3]


def _card_height_for_label(label: str) -> int:
    return 90 + max(0, len(_wrap_lines(label)) - 1) * 20


def build_family_tree_image(
    *,
    subject_label: str,
    parents: list[str],
    step_parents: list[str] | None = None,
    spouse: str | None,
    siblings: list[str] | None = None,
    children: list[str],
    pets: list[str],
    grandparents: list[str] | None = None,
) -> bytes:
    from PIL import Image, ImageDraw

    direct_parents = parents[:2]
    indirect_parents = (step_parents or [])[:2]
    sibling_labels = (siblings or [])[:5]
    grandparent_labels = (grandparents or [])[:4]
    lower_generation = [*children[:8], *pets[:6]]
    top_count = max(1, len(direct_parents) + len(indirect_parents))
    bottom_count = max(1, len(lower_generation))
    content_slots = max(3, top_count, bottom_count)
    width = max(1320, 280 + content_slots * 220)
    height = 980

    image = Image.new("RGBA", (width, height), _FIGURE_BG)
    draw = ImageDraw.Draw(image)

    title_font = _load_font(34, bold=True)
    section_font = _load_font(20)
    name_font = _load_font(20, bold=True)
    small_font = _load_font(15)
    chip_font = _load_font(15, bold=True)
    emoji_font_cache: dict[int, tuple[object, int] | None] = {}
    emoji_render_cache: dict[tuple[str, int, str], Image.Image | None] = {}

    def _font_size(font, fallback: int = 16) -> int:
        return int(getattr(font, "size", fallback))

    def _line_height(font) -> int:
        _left, _top, _right, bottom = draw.textbbox((0, 0), "Ag", font=font)
        return bottom - _top

    def _get_emoji_font(font):
        size = _font_size(font)
        if size not in emoji_font_cache:
            emoji_font_cache[size] = _load_emoji_font(size)
        return emoji_font_cache[size]

    def _render_emoji(text: str, *, font, fill: str) -> Image.Image | None:
        size = _font_size(font)
        cache_key = (text, size, fill)
        if cache_key in emoji_render_cache:
            cached = emoji_render_cache[cache_key]
            return None if cached is None else cached.copy()

        emoji_font_info = _get_emoji_font(font)
        if emoji_font_info is None:
            emoji_render_cache[cache_key] = None
            return None

        emoji_font, load_size = emoji_font_info
        measure = Image.new("RGBA", (1, 1), (0, 0, 0, 0))
        measure_draw = ImageDraw.Draw(measure)
        try:
            bbox = measure_draw.textbbox((0, 0), text, font=emoji_font, embedded_color=True)
        except TypeError:
            bbox = measure_draw.textbbox((0, 0), text, font=emoji_font)

        width_local = max(1, bbox[2] - bbox[0])
        height_local = max(1, bbox[3] - bbox[1])
        temp = Image.new("RGBA", (width_local + 8, height_local + 8), (0, 0, 0, 0))
        temp_draw = ImageDraw.Draw(temp)
        try:
            temp_draw.text((4 - bbox[0], 4 - bbox[1]), text, font=emoji_font, fill=fill, embedded_color=True)
        except TypeError:
            temp_draw.text((4 - bbox[0], 4 - bbox[1]), text, font=emoji_font, fill=fill)

        alpha_bbox = temp.getbbox()
        if alpha_bbox is None:
            emoji_render_cache[cache_key] = None
            return None

        rendered = temp.crop(alpha_bbox)
        if load_size != size:
            scale = size / load_size
            rendered = rendered.resize(
                (
                    max(1, round(rendered.width * scale)),
                    max(1, round(rendered.height * scale)),
                ),
                Image.Resampling.LANCZOS,
            )

        emoji_render_cache[cache_key] = rendered
        return rendered.copy()

    def _measure_text(text: str, *, font) -> tuple[int, int]:
        if not text:
            return (0, _line_height(font))

        width_local = 0
        height_local = _line_height(font)
        for segment, is_emoji in _split_text_runs(text):
            if not segment:
                continue
            if is_emoji:
                rendered = _render_emoji(segment, font=font, fill=_TEXT_MAIN)
                if rendered is not None:
                    width_local += rendered.width
                    height_local = max(height_local, rendered.height)
                    continue
            bbox = draw.textbbox((0, 0), segment, font=font)
            width_local += max(0, bbox[2] - bbox[0])
            height_local = max(height_local, bbox[3] - bbox[1])
        return width_local, height_local

    def _draw_text(x: float, y: float, text: str, *, fill: str, font) -> tuple[int, int]:
        width_local, height_local = _measure_text(text, font=font)
        cursor_x = x
        line_height = _line_height(font)
        for segment, is_emoji in _split_text_runs(text):
            if not segment:
                continue
            if is_emoji:
                rendered = _render_emoji(segment, font=font, fill=fill)
                if rendered is not None:
                    emoji_y = y + max(0, (line_height - rendered.height) / 2)
                    image.alpha_composite(rendered, dest=(int(round(cursor_x)), int(round(emoji_y))))
                    cursor_x += rendered.width
                    continue
            draw.text((cursor_x, y), segment, fill=fill, font=font)
            bbox = draw.textbbox((0, 0), segment, font=font)
            cursor_x += max(0, bbox[2] - bbox[0])
        return width_local, height_local

    _draw_text(64, 48, "Семья и династия", fill=_TEXT_MAIN, font=title_font)

    def _text_height(text: str, font) -> int:
        return _measure_text(text, font=font)[1]

    def draw_pill(
        x: int,
        y: int,
        text: str,
        *,
        outline: str,
        font,
        text_fill: str = _TEXT_MAIN,
        fill: str = _PANEL_BG,
        padding_x: int = 14,
        height_local: int = 34,
    ) -> int:
        text_width, text_height = _measure_text(text, font=font)
        width_local = text_width + padding_x * 2
        draw.rounded_rectangle(
            (x, y, x + width_local, y + height_local),
            radius=height_local // 2,
            fill=fill,
            outline=outline,
            width=2,
        )
        _draw_text(
            x + (width_local - text_width) / 2,
            y + (height_local - text_height) / 2 - 1,
            text,
            fill=text_fill,
            font=font,
        )
        return width_local

    def draw_badge(x: int, y: int, text: str, *, outline: str = _GRID, text_fill: str = _TEXT_MUTED) -> int:
        return draw_pill(
            x,
            y,
            text,
            outline=outline,
            font=small_font,
            text_fill=text_fill,
            padding_x=12,
            height_local=28,
        )

    def draw_card(
        x: int,
        y: int,
        *,
        label: str,
        subtitle: str,
        outline: str,
        text_fill: str = _TEXT_MAIN,
        subtitle_fill: str = _TEXT_MUTED,
    ) -> tuple[int, int, int, int]:
        card_height = _card_height_for_label(label)
        draw.rounded_rectangle(
            (x, y, x + _CARD_WIDTH, y + card_height),
            radius=_CARD_RADIUS,
            fill=_PANEL_BG,
            outline=outline,
            width=_CARD_OUTLINE_WIDTH,
        )
        _draw_text(x + 18, y + 16, subtitle, fill=subtitle_fill, font=small_font)
        for index, line in enumerate(_wrap_lines(label)):
            _draw_text(
                x + 18,
                y + 40 + index * 24,
                line,
                fill=text_fill,
                font=name_font if text_fill == _TEXT_MAIN else small_font,
            )
        return (x, y, x + _CARD_WIDTH, y + card_height)

    def draw_dashed_line(start: tuple[int, int], end: tuple[int, int], *, color: str, width_local: int = 3) -> None:
        segments = 14
        for index in range(segments):
            if index % 2:
                continue
            x0 = start[0] + (end[0] - start[0]) * index / segments
            y0 = start[1] + (end[1] - start[1]) * index / segments
            x1 = start[0] + (end[0] - start[0]) * (index + 1) / segments
            y1 = start[1] + (end[1] - start[1]) * (index + 1) / segments
            draw.line((x0, y0, x1, y1), fill=color, width=width_local)

    def centered_positions(count: int, *, row_width: int, card_width: int, gap: int = 28) -> list[int]:
        total_width = count * card_width + max(0, count - 1) * gap
        start_x = 64 + max(0, (row_width - total_width) // 2)
        return [start_x + index * (card_width + gap) for index in range(count)]

    content_width = width - 128
    stats_y = 100
    top_y = 188
    subject_y = 430
    bottom_y = 724

    stat_x = 64
    for text, outline in (
        (f"Родителей: {len(direct_parents)}", _ACCENT_CYAN),
        (f"Партнёров: {1 if spouse else 0}", _ACCENT_GOLD),
        (f"Детей: {len(children)}", _ACCENT_VIOLET),
        (f"Питомцев: {len(pets)}", _ACCENT_ROSE),
    ):
        stat_x += draw_pill(stat_x, stats_y, text, outline=outline, font=chip_font) + 12

    _draw_text(64, top_y - 34, "Родители и супруги", fill=_TEXT_MUTED, font=section_font)
    top_cards: list[tuple[int, int, int, int]] = []
    top_people = [*(("Родитель", label) for label in direct_parents), *(("Отчим/мачеха", label) for label in indirect_parents)]
    top_positions = centered_positions(max(1, len(top_people)), row_width=content_width, card_width=_CARD_WIDTH)
    if top_people:
        for index, (subtitle, label) in enumerate(top_people):
            top_cards.append(draw_card(top_positions[index], top_y, label=label, subtitle=subtitle, outline=_ACCENT_VIOLET))
    else:
        top_cards.append(
            draw_card(
                top_positions[0],
                top_y,
                label="Связи ещё не заданы",
                subtitle="Родители",
                outline=_GRID,
                text_fill=_TEXT_MUTED,
                subtitle_fill=_TEXT_MUTED,
            )
        )

    if grandparent_labels:
        chip_x = 64
        chip_y = top_y + 142
        _draw_text(chip_x, chip_y, "Предки", fill=_TEXT_MUTED, font=small_font)
        chip_cursor = chip_x
        for label in grandparent_labels:
            chip_cursor += draw_badge(chip_cursor, chip_y + 26, label) + 10

    subject_box = draw_card(
        max(96, width // 2 - (_CARD_WIDTH // 2)),
        subject_y,
        label=subject_label,
        subtitle="Центр династии",
        outline=_ACCENT_CYAN,
    )
    spouse_box = None
    if spouse:
        spouse_box = draw_card(
            subject_box[2] + 34,
            subject_y + 12,
            label=spouse,
            subtitle="Супруг(а)",
            outline=_ACCENT_GOLD,
        )
        draw_dashed_line(
            (subject_box[2], subject_box[1] + 42),
            (spouse_box[0], spouse_box[1] + 42),
            color=_ACCENT_GOLD,
            width_local=_DASHED_WIDTH,
        )

    if sibling_labels:
        _draw_text(64, subject_y + 12, "Братья и сёстры", fill=_TEXT_MUTED, font=section_font)
        chip_x = 64
        chip_y = subject_y + 52
        for label in sibling_labels:
            chip_x += draw_badge(chip_x, chip_y, label) + 10

    lower_people = [*(("Ребёнок", label) for label in children[:8]), *(("Питомец", label) for label in pets[:6])]
    lower_positions = centered_positions(max(1, len(lower_people)), row_width=content_width, card_width=_CARD_WIDTH)
    _draw_text(64, bottom_y - 34, "Дети и питомцы", fill=_TEXT_MUTED, font=section_font)
    lower_cards: list[tuple[int, int, int, int]] = []
    if lower_people:
        for index, (subtitle, label) in enumerate(lower_people):
            lower_cards.append(draw_card(lower_positions[index], bottom_y, label=label, subtitle=subtitle, outline=_ACCENT_VIOLET))
    else:
        lower_cards.append(
            draw_card(
                lower_positions[0],
                bottom_y,
                label="Потомков нет",
                subtitle="Дети и питомцы",
                outline=_GRID,
                text_fill=_TEXT_MUTED,
                subtitle_fill=_TEXT_MUTED,
            )
        )

    top_anchor_x = subject_box[0] + (subject_box[2] - subject_box[0]) // 2
    for card in top_cards:
        parent_anchor_x = card[0] + (card[2] - card[0]) // 2
        draw.line((parent_anchor_x, card[3], parent_anchor_x, subject_y - 42), fill=_ACCENT_CYAN, width=_LINE_WIDTH)
        draw.line((parent_anchor_x, subject_y - 42, top_anchor_x, subject_y - 42), fill=_ACCENT_CYAN, width=_LINE_WIDTH)
    draw.line((top_anchor_x, subject_y - 42, top_anchor_x, subject_box[1]), fill=_ACCENT_CYAN, width=_LINE_WIDTH)

    child_anchor_y = subject_box[3] + 24
    draw.line((top_anchor_x, subject_box[3], top_anchor_x, child_anchor_y), fill=_ACCENT_CYAN, width=_LINE_WIDTH)
    if lower_cards:
        branch_y = child_anchor_y + 42
        draw.line((top_anchor_x, child_anchor_y, top_anchor_x, branch_y), fill=_ACCENT_CYAN, width=_LINE_WIDTH)
        left_x = lower_cards[0][0] + (lower_cards[0][2] - lower_cards[0][0]) // 2
        right_x = lower_cards[-1][0] + (lower_cards[-1][2] - lower_cards[-1][0]) // 2
        draw.line((left_x, branch_y, right_x, branch_y), fill=_ACCENT_CYAN, width=_LINE_WIDTH)
        for card in lower_cards:
            anchor_x = card[0] + (card[2] - card[0]) // 2
            draw.line((anchor_x, branch_y, anchor_x, card[1]), fill=_ACCENT_CYAN, width=_LINE_WIDTH)

    for index in range(min(len(direct_parents), len(indirect_parents))):
        direct_box = top_cards[index]
        indirect_box = top_cards[len(direct_parents) + index]
        draw_dashed_line(
            (direct_box[2], direct_box[1] + 28),
            (indirect_box[0], indirect_box[1] + 28),
            color=_ACCENT_GOLD,
            width_local=2,
        )

    _draw_text(width - 272, height - 54, "Selara • family graph", fill=_TEXT_MUTED, font=small_font)

    buffer = BytesIO()
    image.convert("RGB").save(buffer, format="PNG")
    return buffer.getvalue()
