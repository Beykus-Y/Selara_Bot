from __future__ import annotations

import json
import subprocess
import tempfile
import time
from functools import lru_cache
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

from selara.presentation import gacha_reel
from selara.presentation.gacha_reel import ReelCard, build_gacha_reel_animation


@lru_cache(maxsize=1)
def _has_libx264() -> bool:
    """MP4 encoding needs an H.264 encoder in the local `ffmpeg` build.
    Debian's `ffmpeg` package (what CI and the production Docker image use)
    ships `libx264`; some other distros' ffmpeg builds don't (e.g. Fedora's
    default `ffmpeg-free` package omits it entirely) — skip locally on those
    rather than falsely reporting a code bug."""
    try:
        result = subprocess.run(["ffmpeg", "-encoders"], capture_output=True, text=True, timeout=10)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    return "libx264" in result.stdout


requires_libx264 = pytest.mark.skipif(
    not _has_libx264(), reason="no libx264 encoder available in the local ffmpeg build"
)


def _solid_png(color: tuple[int, int, int], size: tuple[int, int] = (200, 200)) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", size, color).save(buffer, format="PNG")
    return buffer.getvalue()


def _ffprobe(video_bytes: bytes) -> dict:
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "reel.mp4"
        path.write_bytes(video_bytes)
        result = subprocess.run(
            [
                "ffprobe", "-v", "error", "-print_format", "json",
                "-show_format", "-show_streams", str(path),
            ],
            capture_output=True, text=True, check=True,
        )
        return json.loads(result.stdout)


@requires_libx264
def test_build_gacha_reel_animation_returns_mp4_in_target_duration() -> None:
    """Reel-animation renderer for the animated gacha pull mode (see
    docs/GACHA_MODERNIZATION_TODO.md, Этап 3/раздел 17): renders frames with
    Pillow, then encodes via ffmpeg as MP4 (H.264, no audio) instead of GIF —
    a ~9-11MB GIF at this resolution/duration made Telegram clients show it
    as a downloadable file instead of an inline-autoplay animation; MP4 is
    Telegram's own recommended format for `send_animation` and encodes the
    same visual content at a fraction of the size."""
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

    probe = _ffprobe(rendered)
    video_streams = [s for s in probe["streams"] if s["codec_type"] == "video"]
    assert len(video_streams) == 1
    stream = video_streams[0]
    assert stream["codec_name"] in ("h264",)
    assert (stream["width"], stream["height"]) == (1280, 720)
    assert not any(s["codec_type"] == "audio" for s in probe["streams"])  # no sound track

    duration_ms = float(probe["format"]["duration"]) * 1000
    # Must comfortably fill the 12s hold before the message is deleted
    # (_GACHA_ANIMATION_HOLD_SECONDS) without completing a full extra
    # loop — that looked like the animation "jumping back to the start"
    # (user feedback, 2026-08-19).
    assert 11000 <= duration_ms <= 12500

    # Reasonably small — the whole point of switching from GIF was to get
    # well under whatever size threshold makes Telegram clients fall back
    # to file-download rendering instead of inline autoplay.
    assert len(rendered) < 5_000_000


@requires_libx264
def test_encode_stops_composing_and_kills_ffmpeg_when_the_deadline_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    """A render that overruns its deadline must not keep composing frames or
    keep an ffmpeg child alive behind the caller (incident 2026-10-12)."""
    monkeypatch.setattr(gacha_reel, "_ENCODE_TIMEOUT_SECONDS", 0.5)
    composed = []

    def slow_frames():
        for _ in range(400):
            time.sleep(0.05)
            composed.append(1)
            yield Image.new("RGB", (1280, 720), (10, 20, 30))

    with pytest.raises(gacha_reel.GachaReelEncodeError, match="did not finish"):
        gacha_reel._encode_frames_to_mp4(slow_frames(), durations=[100] * 400)

    assert len(composed) < 400


def test_build_gacha_reel_animation_requires_at_least_one_filler() -> None:
    background = _solid_png((20, 10, 40), size=(1024, 576))
    landing = ReelCard(name="X", rarity_label="⬜", border_color=(1, 1, 1), image_bytes=_solid_png((1, 1, 1)))

    try:
        build_gacha_reel_animation(background_bytes=background, landing_card=landing, filler_cards=[])
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for empty filler_cards")
