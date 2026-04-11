from __future__ import annotations

from io import BytesIO

from PIL import Image

from selara.presentation.quote_card import build_quote_card


def test_build_quote_card_returns_png_with_background_size() -> None:
    avatar = BytesIO()
    Image.new("RGB", (96, 96), "#5f8cff").save(avatar, format="PNG")

    rendered = build_quote_card(
        author_name="Юлий #LL 🦋",
        quote_text="в камине в 6 утра..",
        date_label="11.04.2026",
        avatar_bytes=avatar.getvalue(),
    )

    with Image.open(BytesIO(rendered)) as image:
        assert image.format == "PNG"
        assert image.size == (1720, 1140)
