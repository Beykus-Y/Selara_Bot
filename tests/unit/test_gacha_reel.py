from __future__ import annotations

from io import BytesIO

from PIL import Image

from selara.presentation.gacha_reel import ReelCard, build_gacha_reel_animation


def _solid_png(color: tuple[int, int, int], size: tuple[int, int] = (200, 200)) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", size, color).save(buffer, format="PNG")
    return buffer.getvalue()


def test_build_gacha_reel_animation_returns_multi_frame_gif_in_target_duration() -> None:
    """Reel-animation renderer for the animated gacha pull mode (see
    docs/GACHA_MODERNIZATION_TODO.md, Этап 3): pure Pillow, no ffmpeg,
    background + card strip -> animated GIF. Timing/frame-rate tuned after
    live user feedback (2026-08-19): smoother motion (more frames), higher
    resolution, longer total playtime to comfortably fill the extended
    12s hold before the message is deleted."""
    background = _solid_png((20, 10, 40), size=(1024, 576))
    landing = ReelCard(
        name="Фурина",
        rarity_label="🟪 Мифическая",
        border_color=(186, 85, 255),
        image_bytes=_solid_png((255, 200, 200)),
    )
    # A real banner always has enough cards to fill a scrolling strip wider
    # than the viewport (Genshin: 116, HSR: 79) — 10 is representative.
    fillers = [
        ReelCard(
            name=f"Персонаж {i}",
            rarity_label="🟨 Легендарная",
            border_color=(255, 200, 0),
            image_bytes=_solid_png((10 * i % 255, 20 * i % 255, 30 * i % 255)),
        )
        for i in range(10)
    ]

    rendered = build_gacha_reel_animation(background_bytes=background, landing_card=landing, filler_cards=fillers)

    with Image.open(BytesIO(rendered)) as image:
        assert image.format == "GIF"
        assert image.is_animated
        frame_count = image.n_frames
        assert frame_count >= 40  # smoother motion per user feedback (2026-08-19)
        assert image.size == (1280, 720)  # higher resolution per user feedback (2026-08-19)

        total_duration_ms = 0
        for frame_index in range(frame_count):
            image.seek(frame_index)
            total_duration_ms += image.info.get("duration", 0)
        # Must comfortably fill the 12s hold before the message is deleted
        # (_GACHA_ANIMATION_HOLD_SECONDS) without completing a full extra
        # loop — that looked like the animation "jumping back to the start"
        # (user feedback, 2026-08-19).
        assert 11000 <= total_duration_ms <= 12000


def test_build_gacha_reel_animation_requires_at_least_one_filler() -> None:
    background = _solid_png((20, 10, 40), size=(1024, 576))
    landing = ReelCard(name="X", rarity_label="⬜", border_color=(1, 1, 1), image_bytes=_solid_png((1, 1, 1)))

    try:
        build_gacha_reel_animation(background_bytes=background, landing_card=landing, filler_cards=[])
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for empty filler_cards")
