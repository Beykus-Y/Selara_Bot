from __future__ import annotations

from io import BytesIO
from pathlib import Path

from selara.presentation.family_tree import _load_emoji_font, _load_font, _split_text_runs

_BACKGROUND_PATH = Path(__file__).resolve().parents[1] / "images" / "citata.png"
_PANEL_FILL = (7, 13, 24, 120)
_SIDEBAR_FILL = (8, 15, 27, 156)
_QUOTE_PANEL_FILL = (9, 16, 28, 94)
_OUTLINE = (255, 255, 255, 34)
_TEXT_MAIN = (244, 247, 255, 255)
_TEXT_MUTED = (186, 198, 220, 255)
_ACCENT = (121, 208, 255, 255)
_SHADOW = (0, 0, 0, 160)
_AVATAR_RING = (255, 255, 255, 72)


def _font_size(font, fallback: int = 16) -> int:
    return int(getattr(font, "size", fallback))


def _resampling_lanczos():
    from PIL import Image

    return getattr(getattr(Image, "Resampling", Image), "LANCZOS")


def _line_height(draw, font) -> int:
    _left, top, _right, bottom = draw.textbbox((0, 0), "Ag", font=font)
    return max(1, bottom - top)


def _truncate_text(value: str, *, max_len: int) -> str:
    compact = " ".join((value or "").split()).strip()
    if len(compact) <= max_len:
        return compact
    return compact[: max(1, max_len - 1)].rstrip() + "…"


def _render_emoji(text: str, *, draw, font, fill: tuple[int, int, int, int]):
    from PIL import Image, ImageDraw

    emoji_font_info = _load_emoji_font(_font_size(font))
    if emoji_font_info is None:
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
        return None

    rendered = temp.crop(alpha_bbox)
    size = _font_size(font)
    if load_size != size:
        scale = size / load_size
        rendered = rendered.resize(
            (
                max(1, round(rendered.width * scale)),
                max(1, round(rendered.height * scale)),
            ),
            _resampling_lanczos(),
        )
    return rendered


def _measure_text(draw, text: str, *, font) -> tuple[int, int]:
    if not text:
        return 0, _line_height(draw, font)

    width_local = 0
    height_local = _line_height(draw, font)
    for segment, is_emoji in _split_text_runs(text):
        if not segment:
            continue
        if is_emoji:
            rendered = _render_emoji(segment, draw=draw, font=font, fill=_TEXT_MAIN)
            if rendered is not None:
                width_local += rendered.width
                height_local = max(height_local, rendered.height)
                continue
        bbox = draw.textbbox((0, 0), segment, font=font)
        width_local += max(0, bbox[2] - bbox[0])
        height_local = max(height_local, bbox[3] - bbox[1])
    return width_local, height_local


def _draw_text(image, draw, x: float, y: float, text: str, *, fill: tuple[int, int, int, int], font) -> tuple[int, int]:
    width_local, height_local = _measure_text(draw, text, font=font)
    cursor_x = x
    line_height = _line_height(draw, font)
    for segment, is_emoji in _split_text_runs(text):
        if not segment:
            continue
        if is_emoji:
            rendered = _render_emoji(segment, draw=draw, font=font, fill=fill)
            if rendered is not None:
                emoji_y = y + max(0, (line_height - rendered.height) / 2)
                image.alpha_composite(rendered, dest=(int(round(cursor_x)), int(round(emoji_y))))
                cursor_x += rendered.width
                continue
        draw.text((cursor_x, y), segment, fill=fill, font=font)
        bbox = draw.textbbox((0, 0), segment, font=font)
        cursor_x += max(0, bbox[2] - bbox[0])
    return width_local, height_local


def _draw_shadowed_text(image, draw, x: float, y: float, text: str, *, fill, font) -> tuple[int, int]:
    _draw_text(image, draw, x + 2, y + 3, text, fill=_SHADOW, font=font)
    return _draw_text(image, draw, x, y, text, fill=fill, font=font)


def _draw_centered_text(image, draw, text: str, *, font, fill, x0: int, x1: int, y: float) -> tuple[int, int]:
    width_local, height_local = _measure_text(draw, text, font=font)
    x = x0 + max(0, (x1 - x0 - width_local) / 2)
    _draw_shadowed_text(image, draw, x, y, text, fill=fill, font=font)
    return width_local, height_local


def _split_long_word(draw, token: str, *, max_width: int, font) -> list[str]:
    pieces: list[str] = []
    current = ""
    for char in token:
        candidate = f"{current}{char}"
        if current and _measure_text(draw, candidate, font=font)[0] > max_width:
            pieces.append(current)
            current = char
            continue
        current = candidate
    if current:
        pieces.append(current)
    return pieces or [token]


def _fit_with_ellipsis(draw, text: str, *, max_width: int, font) -> str:
    candidate = text.rstrip()
    if not candidate:
        return "…"
    while candidate:
        candidate = candidate.rstrip()
        if _measure_text(draw, f"{candidate}…", font=font)[0] <= max_width:
            return f"{candidate}…"
        candidate = candidate[:-1]
    return "…"


def _wrap_text(draw, text: str, *, max_width: int, max_lines: int, font) -> list[str]:
    normalized = "\n".join(line.strip() for line in (text or "").replace("\r", "\n").split("\n"))
    paragraphs = [line for line in normalized.split("\n") if line] or ["-"]
    lines: list[str] = []

    for paragraph in paragraphs:
        words = [word for word in paragraph.split(" ") if word]
        current = ""
        while words:
            word = words.pop(0)
            candidate = word if not current else f"{current} {word}"
            if _measure_text(draw, candidate, font=font)[0] <= max_width:
                current = candidate
                continue

            if current:
                lines.append(current)
                current = ""
                words.insert(0, word)
                if len(lines) >= max_lines:
                    return lines
                continue

            pieces = _split_long_word(draw, word, max_width=max_width, font=font)
            first, rest = pieces[0], pieces[1:]
            lines.append(first)
            words = rest + words
            if len(lines) >= max_lines:
                return lines
        if current:
            lines.append(current)
            if len(lines) >= max_lines:
                return lines
    return lines[:max_lines]


def _quote_layout(draw, quote_text: str, *, max_width: int, max_height: int):
    candidate_sizes = (86, 80, 74, 68, 62, 58, 54, 50, 46)
    fallback_font = _load_font(46, bold=True)
    fallback_lines = _wrap_text(draw, quote_text, max_width=max_width, max_lines=6, font=fallback_font)
    fallback_gap = 18

    for size in candidate_sizes:
        font = _load_font(size, bold=True)
        lines = _wrap_text(draw, quote_text, max_width=max_width, max_lines=6, font=font)
        gap = max(14, size // 4)
        total_height = 0
        for index, line in enumerate(lines):
            total_height += _measure_text(draw, line, font=font)[1]
            if index:
                total_height += gap
        if total_height <= max_height and len(lines) <= 6:
            return font, lines, gap
        fallback_font = font
        fallback_lines = lines
        fallback_gap = gap

    if len(fallback_lines) == 6:
        fallback_lines[-1] = _fit_with_ellipsis(draw, fallback_lines[-1], max_width=max_width, font=fallback_font)
    return fallback_font, fallback_lines, fallback_gap


def _initials(value: str) -> str:
    parts = [part for part in (value or "").replace("@", "").split() if part]
    if not parts:
        return "?"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return f"{parts[0][:1]}{parts[1][:1]}".upper()


def _avatar_image(*, avatar_bytes: bytes | None, size: int, initials: str):
    from PIL import Image, ImageDraw, ImageOps

    if avatar_bytes:
        try:
            with Image.open(BytesIO(avatar_bytes)) as source:
                avatar = ImageOps.fit(
                    source.convert("RGBA"),
                    (size, size),
                    method=_resampling_lanczos(),
                    centering=(0.5, 0.5),
                )
        except Exception:
            avatar = None
    else:
        avatar = None

    if avatar is None:
        avatar = Image.new("RGBA", (size, size), (22, 33, 53, 255))
        placeholder_draw = ImageDraw.Draw(avatar)
        placeholder_draw.ellipse((0, 0, size - 1, size - 1), fill=(28, 43, 69, 255))
        placeholder_draw.ellipse((14, 14, size - 15, size - 15), outline=_ACCENT, width=4)
        initials_font = _load_font(max(28, size // 3), bold=True)
        left, top, right, bottom = placeholder_draw.textbbox((0, 0), initials, font=initials_font)
        placeholder_draw.text(
            ((size - (right - left)) / 2 - left, (size - (bottom - top)) / 2 - top - 4),
            initials,
            fill=_TEXT_MAIN,
            font=initials_font,
        )

    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size - 1, size - 1), fill=255)
    framed = Image.new("RGBA", (size + 28, size + 28), (0, 0, 0, 0))
    frame_draw = ImageDraw.Draw(framed)
    frame_draw.ellipse((0, 0, size + 27, size + 27), fill=(0, 0, 0, 92), outline=_AVATAR_RING, width=2)
    framed.paste(avatar, (14, 14), mask)
    return framed


def build_quote_card(
    *,
    author_name: str,
    quote_text: str,
    date_label: str,
    avatar_bytes: bytes | None = None,
) -> bytes:
    from PIL import Image, ImageDraw

    with Image.open(_BACKGROUND_PATH) as source:
        image = source.convert("RGBA")

    width, height = image.size
    overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    overlay_draw = ImageDraw.Draw(overlay)
    overlay_draw.rounded_rectangle(
        (72, 72, width - 72, height - 72),
        radius=58,
        fill=_PANEL_FILL,
        outline=_OUTLINE,
        width=2,
    )
    overlay_draw.rounded_rectangle((104, 104, 540, height - 104), radius=44, fill=_SIDEBAR_FILL)
    overlay_draw.rounded_rectangle((592, 142, width - 118, height - 142), radius=44, fill=_QUOTE_PANEL_FILL)
    overlay_draw.rounded_rectangle((138, 236, 146, height - 176), radius=4, fill=_ACCENT)
    image = Image.alpha_composite(image, overlay)
    draw = ImageDraw.Draw(image)

    eyebrow_font = _load_font(30, bold=False)
    name_font = _load_font(58, bold=True)
    date_font = _load_font(34, bold=False)
    quote_mark_font = _load_font(166, bold=True)

    author_label = _truncate_text(author_name, max_len=30) or "Без имени"
    date_value = _truncate_text(date_label, max_len=20) or "-"
    quote_value = (quote_text or "").strip() or "-"

    _draw_shadowed_text(image, draw, 160, 128, "цитата", fill=_ACCENT, font=eyebrow_font)
    _draw_shadowed_text(image, draw, 160, 176, author_label, fill=_TEXT_MAIN, font=name_font)

    avatar = _avatar_image(
        avatar_bytes=avatar_bytes,
        size=258,
        initials=_initials(author_label),
    )
    image.alpha_composite(avatar, dest=(148, 492))

    _draw_shadowed_text(image, draw, 164, 852, date_value, fill=_TEXT_MUTED, font=date_font)

    quote_x0 = 662
    quote_x1 = width - 170
    quote_y0 = 258
    quote_y1 = height - 214
    quote_width = quote_x1 - quote_x0
    quote_height = quote_y1 - quote_y0

    quote_font, quote_lines, gap = _quote_layout(draw, quote_value, max_width=quote_width, max_height=quote_height - 40)
    total_text_height = 0
    line_heights: list[int] = []
    for index, line in enumerate(quote_lines):
        line_height = _measure_text(draw, line, font=quote_font)[1]
        line_heights.append(line_height)
        total_text_height += line_height
        if index:
            total_text_height += gap
    cursor_y = quote_y0 + max(0, (quote_height - total_text_height) / 2)

    _draw_shadowed_text(image, draw, quote_x0 - 70, quote_y0 - 48, "“", fill=_TEXT_MAIN, font=quote_mark_font)
    _draw_shadowed_text(image, draw, quote_x1 - 36, quote_y1 - 118, "”", fill=_TEXT_MAIN, font=quote_mark_font)

    for index, line in enumerate(quote_lines):
        _draw_centered_text(
            image,
            draw,
            line,
            font=quote_font,
            fill=_TEXT_MAIN,
            x0=quote_x0,
            x1=quote_x1,
            y=cursor_y,
        )
        cursor_y += line_heights[index] + gap

    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()
