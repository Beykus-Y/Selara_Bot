from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from typing import Sequence

from PIL import Image, ImageDraw

from selara.presentation.family_tree import _load_font

_CANVAS_SIZE = (1280, 720)
_CARD_SIZE = (180, 180)
_CARD_GAP = 24
_LABEL_HEIGHT = 56
_LABEL_FONT_SIZE = 30
_VIEWPORT_Y = 320
_FRAME_COUNT = 115
_TOTAL_DURATION_MS = 11500
_HOLD_INTRO_FRAMES = 6
_HOLD_STOP_FRAMES = 15
_HIGHLIGHT_WIDTH = 6


@dataclass(slots=True, frozen=True)
class ReelCard:
    """One portrait shown in the reel. `border_color` renders the rarity
    frame — see docs/GACHA_MODERNIZATION_TODO.md, Этап 3, decision 5."""

    name: str
    rarity_label: str
    border_color: tuple[int, int, int]
    image_bytes: bytes


def _resampling_lanczos():
    return getattr(getattr(Image, "Resampling", Image), "LANCZOS")


def _paste_card(strip: Image.Image, card: ReelCard, *, x: int, font) -> None:
    card_w, card_h = _CARD_SIZE
    portrait = Image.open(BytesIO(card.image_bytes)).convert("RGB")
    portrait = portrait.resize((card_w, card_h), _resampling_lanczos())
    strip.paste(portrait, (x, 0))

    draw = ImageDraw.Draw(strip)
    draw.rectangle([x, 0, x + card_w - 1, card_h - 1], outline=card.border_color, width=4)

    label = card.name if len(card.name) <= 12 else card.name[:11] + "…"
    text_width = draw.textlength(label, font=font)
    draw.text(
        (x + card_w / 2 - text_width / 2, card_h + 10),
        label,
        font=font,
        fill=(255, 255, 255, 255),
    )


def _build_offset_schedule(*, final_offset: int, frame_count: int) -> list[int]:
    moving_frames = frame_count - _HOLD_INTRO_FRAMES - _HOLD_STOP_FRAMES
    offsets = [0] * _HOLD_INTRO_FRAMES
    for i in range(moving_frames):
        t = (i + 1) / moving_frames
        eased = 1 - (1 - t) ** 2  # ease-out: fast start, slow finish
        offsets.append(int(final_offset * eased))
    offsets.extend([final_offset] * _HOLD_STOP_FRAMES)
    return offsets


def _build_duration_schedule(*, frame_count: int, total_ms: int) -> list[int]:
    base = total_ms // frame_count
    durations = [base] * frame_count
    durations[-1] += total_ms - base * frame_count
    return durations


def _draw_highlight(frame: Image.Image, *, x: int, y: int, size: tuple[int, int], color: tuple[int, int, int]) -> None:
    draw = ImageDraw.Draw(frame)
    width, height = size
    for i in range(_HIGHLIGHT_WIDTH):
        draw.rectangle([x - i, y - i, x + width + i, y + height + i], outline=color)


def build_gacha_reel_animation(
    *,
    background_bytes: bytes,
    landing_card: ReelCard,
    filler_cards: Sequence[ReelCard],
) -> bytes:
    """Compose the reel-scroll animation for one already-known pull result.

    `filler_cards` are decorative — they must never be the actual pull
    result and never determine it; the caller passes an already-computed
    `landing_card`. The landing card sits at a fixed index with enough
    trailing filler cards after it (cycled from `filler_cards`) that it can
    always be fully centered in the final frame, never clamped against the
    strip's edge.
    """
    if not filler_cards:
        raise ValueError("At least one filler card is required for the reel.")

    card_w, card_h = _CARD_SIZE
    viewport_w = _CANVAS_SIZE[0] - 80
    trailing_count = -(-(viewport_w // 2) // (card_w + _CARD_GAP))  # ceil division
    trailing_cards = [filler_cards[i % len(filler_cards)] for i in range(trailing_count)]
    cards = [*filler_cards, landing_card, *trailing_cards]
    landing_index = len(filler_cards)

    background = Image.open(BytesIO(background_bytes)).convert("RGB").resize(_CANVAS_SIZE)

    strip_width = len(cards) * (card_w + _CARD_GAP)
    strip = Image.new("RGBA", (strip_width, card_h + _LABEL_HEIGHT), (0, 0, 0, 0))
    font = _load_font(_LABEL_FONT_SIZE)
    for index, card in enumerate(cards):
        _paste_card(strip, card, x=index * (card_w + _CARD_GAP), font=font)

    viewport_h = card_h + _LABEL_HEIGHT
    landing_center_x = landing_index * (card_w + _CARD_GAP) + card_w / 2
    final_offset = max(0, min(strip_width - viewport_w, int(landing_center_x - viewport_w / 2)))

    offsets = _build_offset_schedule(final_offset=final_offset, frame_count=_FRAME_COUNT)
    durations = _build_duration_schedule(frame_count=_FRAME_COUNT, total_ms=_TOTAL_DURATION_MS)

    paste_x = (background.width - viewport_w) // 2
    rgb_frames: list[Image.Image] = []
    for frame_index, offset in enumerate(offsets):
        frame = background.copy()
        viewport = strip.crop((offset, 0, offset + viewport_w, viewport_h))
        frame.paste(viewport, (paste_x, _VIEWPORT_Y), viewport)
        if frame_index >= len(offsets) - _HOLD_STOP_FRAMES:
            # Track the landing card's true on-screen position rather than
            # assuming it's centered — the final offset is clamped to the
            # strip's right edge (the landing card is last, nothing follows
            # it to fill the right half of the viewport), so a fixed
            # "center of viewport" box would drift off the actual card.
            landing_screen_x = paste_x + (landing_center_x - offset) - card_w / 2
            _draw_highlight(
                frame,
                x=int(landing_screen_x) - 6,
                y=_VIEWPORT_Y - 6,
                size=(card_w + 12, card_h + _LABEL_HEIGHT + 12),
                color=landing_card.border_color,
            )
        rgb_frames.append(frame)

    # A single shared palette (quantized from the busiest frame) lets the
    # GIF encoder delta-encode between frames instead of restating the
    # (mostly static) background every frame — combined with disposal=1
    # ("do not dispose") this cuts file size drastically versus a fresh
    # adaptive palette per frame, without reducing resolution or frame
    # count (both were explicitly requested to go up, not down).
    global_palette = rgb_frames[-1].convert("P", palette=Image.ADAPTIVE, colors=256)
    frames = [frame.quantize(palette=global_palette, dither=Image.Dither.NONE) for frame in rgb_frames]

    buffer = BytesIO()
    frames[0].save(
        buffer,
        format="GIF",
        save_all=True,
        append_images=frames[1:],
        duration=durations,
        loop=0,
        disposal=1,
        optimize=True,
    )
    return buffer.getvalue()
