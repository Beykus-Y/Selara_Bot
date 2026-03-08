from __future__ import annotations

from io import BytesIO
from math import ceil


def _load_font(size: int):
    from PIL import ImageFont

    for candidate in ("DejaVuSans.ttf", "arial.ttf"):
        try:
            return ImageFont.truetype(candidate, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


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

    image = Image.new("RGB", (width, height), "#f4efe4")
    draw = ImageDraw.Draw(image)

    title_font = _load_font(34)
    section_font = _load_font(24)
    name_font = _load_font(20)
    small_font = _load_font(16)

    draw.rounded_rectangle((32, 32, width - 32, height - 32), radius=30, outline="#1c2a39", width=3, fill="#fffaf1")
    draw.text((64, 52), "Семья и династия", fill="#1c2a39", font=title_font)

    accent = "#b85c38"
    line_color = "#4c5b6a"
    card_outline = "#c8b99f"

    def draw_badge(x: int, y: int, text: str, *, fill: str = "#f0e1cb") -> int:
        text_width = int(draw.textlength(text, font=small_font))
        width_local = text_width + 28
        draw.rounded_rectangle((x, y, x + width_local, y + 28), radius=14, fill=fill, outline="#d2c4ad", width=1)
        draw.text((x + 14, y + 6), text, fill="#31404f", font=small_font)
        return width_local

    def draw_card(x: int, y: int, *, label: str, subtitle: str, fill: str) -> tuple[int, int, int, int]:
        card_width = 184
        card_height = _card_height_for_label(label)
        draw.rounded_rectangle((x, y, x + card_width, y + card_height), radius=24, fill=fill, outline=card_outline, width=2)
        draw.text((x + 18, y + 16), subtitle, fill=accent, font=small_font)
        for index, line in enumerate(_wrap_lines(label)):
            draw.text((x + 18, y + 40 + index * 24), line, fill="#0f2438", font=name_font)
        return (x, y, x + card_width, y + card_height)

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
    top_y = 148
    subject_y = 430
    bottom_y = 724

    draw.text((64, top_y - 38), "Родители и супруги", fill="#1c2a39", font=section_font)
    top_cards: list[tuple[int, int, int, int]] = []
    top_people = [*(("Родитель", label, "#dce8d6") for label in direct_parents), *(("Отчим/мачеха", label, "#e9e1f5") for label in indirect_parents)]
    top_positions = centered_positions(max(1, len(top_people)), row_width=content_width, card_width=184)
    if top_people:
        for index, (subtitle, label, fill) in enumerate(top_people):
            top_cards.append(draw_card(top_positions[index], top_y, label=label, subtitle=subtitle, fill=fill))
    else:
        draw_card(top_positions[0], top_y, label="Связи ещё не заданы", subtitle="Родители", fill="#eef2f5")

    if grandparent_labels:
        chip_x = 64
        chip_y = top_y + 138
        draw.text((chip_x, chip_y), "Предки:", fill="#6a5d4d", font=small_font)
        chip_cursor = chip_x + 72
        for label in grandparent_labels:
            chip_cursor += draw_badge(chip_cursor, chip_y - 4, label, fill="#f2dfc7") + 10

    subject_box = draw_card(max(96, width // 2 - 92), subject_y, label=subject_label, subtitle="Центр династии", fill="#dde8f7")
    spouse_box = None
    if spouse:
        spouse_box = draw_card(subject_box[2] + 34, subject_y + 12, label=spouse, subtitle="Супруг(а)", fill="#ffe4d7")
        draw_dashed_line(
            (subject_box[2], subject_box[1] + 42),
            (spouse_box[0], spouse_box[1] + 42),
            color=accent,
            width_local=3,
        )

    if sibling_labels:
        draw.text((64, subject_y + 12), "Братья и сёстры", fill="#1c2a39", font=section_font)
        chip_x = 64
        chip_y = subject_y + 52
        for label in sibling_labels:
            chip_x += draw_badge(chip_x, chip_y, label, fill="#f6f0d2") + 10

    lower_people = [*(("Ребёнок", label, "#fff0d6") for label in children[:8]), *(("Питомец", label, "#f7dfe3") for label in pets[:6])]
    lower_positions = centered_positions(max(1, len(lower_people)), row_width=content_width, card_width=184)
    draw.text((64, bottom_y - 38), "Дети и питомцы", fill="#1c2a39", font=section_font)
    lower_cards: list[tuple[int, int, int, int]] = []
    if lower_people:
        for index, (subtitle, label, fill) in enumerate(lower_people):
            lower_cards.append(draw_card(lower_positions[index], bottom_y, label=label, subtitle=subtitle, fill=fill))
    else:
        lower_cards.append(draw_card(lower_positions[0], bottom_y, label="Потомков и питомцев пока нет", subtitle="Нижний слой", fill="#eef2f5"))

    top_anchor_x = subject_box[0] + (subject_box[2] - subject_box[0]) // 2
    for card in top_cards:
        if card == subject_box:
            continue
        parent_anchor_x = card[0] + (card[2] - card[0]) // 2
        draw.line((parent_anchor_x, card[3], parent_anchor_x, subject_y - 42), fill=line_color, width=3)
        draw.line((parent_anchor_x, subject_y - 42, top_anchor_x, subject_y - 42), fill=line_color, width=3)
    draw.line((top_anchor_x, subject_y - 42, top_anchor_x, subject_box[1]), fill=line_color, width=3)

    child_anchor_y = subject_box[3] + 24
    draw.line((top_anchor_x, subject_box[3], top_anchor_x, child_anchor_y), fill=line_color, width=3)
    if lower_cards:
        branch_y = child_anchor_y + 42
        draw.line((top_anchor_x, child_anchor_y, top_anchor_x, branch_y), fill=line_color, width=3)
        left_x = lower_cards[0][0] + (lower_cards[0][2] - lower_cards[0][0]) // 2
        right_x = lower_cards[-1][0] + (lower_cards[-1][2] - lower_cards[-1][0]) // 2
        draw.line((left_x, branch_y, right_x, branch_y), fill=line_color, width=3)
        for card in lower_cards:
            anchor_x = card[0] + (card[2] - card[0]) // 2
            draw.line((anchor_x, branch_y, anchor_x, card[1]), fill=line_color, width=3)

    for index in range(min(len(direct_parents), len(indirect_parents))):
        direct_box = top_cards[index]
        indirect_box = top_cards[len(direct_parents) + index]
        draw_dashed_line(
            (direct_box[2], direct_box[1] + 28),
            (indirect_box[0], indirect_box[1] + 28),
            color=accent,
            width_local=2,
        )

    stats_y = height - 112
    draw.text((64, stats_y), "Сводка связей", fill="#1c2a39", font=section_font)
    stat_x = 64
    stat_y = stats_y + 36
    for text, fill in (
        (f"Родителей: {len(direct_parents)}", "#dce8d6"),
        (f"Отчимов/мачех: {len(indirect_parents)}", "#e9e1f5"),
        (f"Детей: {len(children)}", "#fff0d6"),
        (f"Питомцев: {len(pets)}", "#f7dfe3"),
        (f"Сиблингов: {len(sibling_labels)}", "#f6f0d2"),
    ):
        stat_x += draw_badge(stat_x, stat_y, text, fill=fill) + 12

    draw.text((width - 280, height - 54), "Selara • family graph", fill="#6a5d4d", font=small_font)

    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()
