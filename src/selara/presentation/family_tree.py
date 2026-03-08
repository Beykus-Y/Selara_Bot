from __future__ import annotations

from io import BytesIO


def _load_font(size: int):
    from PIL import ImageFont

    for candidate in ("DejaVuSans.ttf", "arial.ttf"):
        try:
            return ImageFont.truetype(candidate, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def _wrap_lines(text: str, *, line_len: int = 22) -> list[str]:
    normalized = " ".join((text or "").split()).strip()
    if not normalized:
        return ["—"]
    words = normalized.split(" ")
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = word if not current else f"{current} {word}"
        if len(candidate) <= line_len:
            current = candidate
            continue
        lines.append(current)
        current = word
    if current:
        lines.append(current)
    return lines[:3]


def build_family_tree_image(
    *,
    subject_label: str,
    grandparents: list[str],
    parents: list[str],
    spouse: str | None,
    children: list[str],
    pets: list[str],
) -> bytes:
    from PIL import Image, ImageDraw

    width = 1400
    height = 900
    image = Image.new("RGB", (width, height), "#f4efe4")
    draw = ImageDraw.Draw(image)

    title_font = _load_font(34)
    section_font = _load_font(24)
    name_font = _load_font(20)
    small_font = _load_font(16)

    draw.rounded_rectangle((32, 32, width - 32, height - 32), radius=28, outline="#1c2a39", width=3, fill="#fffaf1")
    draw.text((64, 52), "Семейное древо", fill="#1c2a39", font=title_font)

    accent = "#b85c38"
    line_color = "#4c5b6a"

    def draw_section_box(x0: int, y0: int, x1: int, y1: int, *, title: str, names: list[str], fill: str) -> None:
        draw.rounded_rectangle((x0, y0, x1, y1), radius=24, fill=fill, outline="#1c2a39", width=2)
        draw.text((x0 + 20, y0 + 16), title, fill="#1c2a39", font=section_font)
        y = y0 + 58
        if not names:
            draw.text((x0 + 22, y), "Пусто", fill="#7b7f86", font=small_font)
            return
        for index, name in enumerate(names[:5], start=1):
            lines = _wrap_lines(name)
            line_height = 22
            block_height = 12 + len(lines) * line_height
            draw.rounded_rectangle(
                (x0 + 16, y, x1 - 16, y + block_height + 10),
                radius=16,
                fill="#ffffff",
                outline="#d2c4ad",
                width=1,
            )
            draw.text((x0 + 28, y + 10), f"{index}.", fill=accent, font=small_font)
            for line_index, line in enumerate(lines):
                draw.text((x0 + 58, y + 8 + line_index * line_height), line, fill="#1f2d3d", font=name_font)
            y += block_height + 24
            if y > y1 - 60:
                break

    draw_section_box(64, 126, 456, 400, title="Предки", names=grandparents, fill="#f2dfc7")
    draw_section_box(504, 126, 896, 400, title="Родители", names=parents, fill="#dce8d6")
    draw_section_box(944, 126, 1336, 400, title="Питомцы", names=pets, fill="#f7dfe3")

    draw.rounded_rectangle((430, 470, 970, 650), radius=28, fill="#dde8f7", outline="#1c2a39", width=3)
    draw.text((462, 498), "Центр династии", fill="#1c2a39", font=section_font)
    for line_index, line in enumerate(_wrap_lines(subject_label, line_len=28)):
        draw.text((462, 544 + line_index * 28), line, fill="#0f2438", font=title_font if line_index == 0 else name_font)
    if spouse:
        draw.text((462, 612), f"Супруг(а): {spouse}", fill=accent, font=name_font)
    else:
        draw.text((462, 612), "Супруг(а): нет", fill="#6d7580", font=name_font)

    draw_section_box(188, 700, 676, 844, title="Дети", names=children, fill="#fff0d6")
    draw_section_box(724, 700, 1212, 844, title="Связи", names=[f"Питомцы: {len(pets)}", f"Дети: {len(children)}"], fill="#eef2f5")

    draw.line((700, 400, 700, 470), fill=line_color, width=4)
    draw.line((432, 560, 320, 700), fill=line_color, width=4)
    draw.line((968, 560, 1080, 700), fill=line_color, width=4)
    draw.line((700, 650, 700, 700), fill=line_color, width=4)

    draw.text((64, height - 56), "Selara • семьи и династии", fill="#6a5d4d", font=small_font)

    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()
