from __future__ import annotations

import asyncio
import hashlib
import logging
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from functools import partial
from pathlib import Path

import httpx
from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import BufferedInputFile

from selara.application.gacha_banners import GACHA_BANNER_CODES
from selara.application.use_cases.gacha import GachaUseCaseError, get_banner_cards
from selara.core.config import Settings
from selara.presentation import gacha_reel
from selara.presentation.gacha_reel import ReelCard, build_gacha_reel_animation

logger = logging.getLogger(__name__)

_ACTIVE_BANNERS = GACHA_BANNER_CODES
_VARIANT_CAP = 3
_REGENERATE_PROBABILITY = 0.15
_FILLER_COUNT = 10

# At most one reel render at a time. A render holds its frames plus an ffmpeg child, so two overlapping renders
# were enough to exceed the container's memory limit on 2026-10-12. The slot is released by the render thread itself,
# so a render that a timeout has abandoned still holds it until the thread really ends.
_RENDER_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="gacha-reel")
_render_slot = threading.BoundedSemaphore(1)
_RENDER_SLOT_POLL_SECONDS = 1.0

# Hard ceiling on a single card's warmup (render + Telegram upload + delete).
# Without this, a hung network call (e.g. a dead-but-not-closed connection
# to Telegram) blocks the whole sequential warmup loop forever with no
# exception and no log line — later cards never get processed until the
# bot is restarted (found live, 2026-08-19: warmup stalled on one card for
# 4+ hours with zero further progress or errors).
_WARMUP_CARD_TIMEOUT_SECONDS = 60.0

# How long a cached catalog/image is trusted without even asking the server
# to revalidate. This bounds worst-case staleness after a deploy to at most
# this many seconds — after it elapses, every use always sends the
# remembered ETag and only reuses the cached body on a genuine 304 (i.e.
# the *content* is still current, confirmed by the server itself, not
# because the entry merely still exists locally).
_FRESH_WINDOW_SECONDS = 60.0


@dataclass(slots=True)
class _CachedCatalogEntry:
    etag: str
    response: object
    cached_at: float


@dataclass(slots=True)
class _CachedImageEntry:
    etag: str
    content: bytes
    cached_at: float


_catalog_cache: dict[str, _CachedCatalogEntry] = {}
_image_cache: dict[str, _CachedImageEntry] = {}


async def _get_banner_catalog(settings: Settings, banner: str):
    now = time.monotonic()
    entry = _catalog_cache.get(banner)
    if entry is not None and (now - entry.cached_at) < _FRESH_WINDOW_SECONDS:
        return entry.response

    response, etag = await get_banner_cards(
        settings, banner=banner, if_none_match=entry.etag if entry is not None else None
    )
    if response is None:
        # 304 only happens when we sent If-None-Match, i.e. entry exists.
        entry.cached_at = now
        return entry.response
    if etag:
        _catalog_cache[banner] = _CachedCatalogEntry(etag=etag, response=response, cached_at=now)
    else:
        _catalog_cache.pop(banner, None)
    return response


async def _fetch_bytes_cached(client: httpx.AsyncClient, url: str) -> bytes:
    now = time.monotonic()
    entry = _image_cache.get(url)
    if entry is not None and (now - entry.cached_at) < _FRESH_WINDOW_SECONDS:
        return entry.content

    headers = {"If-None-Match": entry.etag} if entry is not None else {}
    response = await client.get(url, headers=headers)
    if response.status_code == 304 and entry is not None:
        entry.cached_at = now
        return entry.content
    response.raise_for_status()
    etag = response.headers.get("etag")
    content = response.content
    if etag:
        _image_cache[url] = _CachedImageEntry(etag=etag, content=content, cached_at=now)
    else:
        _image_cache.pop(url, None)
    return content

_BACKGROUND_PATH = Path(__file__).resolve().parents[1] / "images" / "gacha_animation" / "reel_background_genshin.png"

_RARITY_BORDER_COLORS: dict[str, tuple[int, int, int]] = {
    "common": (200, 200, 200),
    "rare": (80, 150, 255),
    "epic": (170, 90, 230),
    "legendary": (255, 190, 60),
    "mythic": (230, 60, 70),
}


def _rarity_border_color(rarity: str) -> tuple[int, int, int]:
    return _RARITY_BORDER_COLORS.get(rarity, (200, 200, 200))


def _background_path_for_banner(banner: str) -> Path:
    # Решение 11: HSR reuses the Genshin background for now — architecture
    # allows swapping this per-banner later without a redesign.
    _ = banner
    return _BACKGROUND_PATH


def _compute_cache_version(*, banner: str, landing_card_etag: str) -> str:
    """Deterministic content hash — never a manually-bumped number. Changing
    the renderer module (any tuning of gacha_reel.py's constants/logic) or
    the background asset changes this for *every* card at once; changing
    only one card's source portrait (its ETag from the gacha service)
    changes it only for that card. See docs/GACHA_MODERNIZATION_TODO.md,
    раздел 10."""
    renderer_source = Path(gacha_reel.__file__).read_bytes()
    background_bytes = _background_path_for_banner(banner).read_bytes()
    payload = renderer_source + b"|" + background_bytes + b"|" + landing_card_etag.encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]


async def _compute_landing_cache_version(settings: Settings, *, banner: str, landing_card_code: str) -> str:
    catalog = await _get_banner_catalog(settings, banner)
    landing_meta = next((card for card in catalog.cards if card.code == landing_card_code), None)
    if landing_meta is None:
        raise GachaUseCaseError(f"Карта {landing_card_code} не найдена в каталоге баннера {banner}.")

    async with httpx.AsyncClient(timeout=settings.gacha_timeout_seconds, follow_redirects=True) as client:
        await _fetch_bytes_cached(client, landing_meta.image_url)
    entry = _image_cache.get(landing_meta.image_url)
    landing_etag = entry.etag if entry is not None else ""
    return _compute_cache_version(banner=banner, landing_card_etag=landing_etag)


def _local_reel_path(*, settings: Settings, banner: str, card_code: str, cache_version: str) -> Path:
    return Path(settings.gacha_reel_cache_dir) / banner / card_code / f"{cache_version}.mp4"


def _is_valid_mp4(content: bytes) -> bool:
    """Cheap structural check — not a full decode, just enough to catch
    truncation/corruption before handing the file to Telegram (pre-deploy
    audit finding, 2026-08-19: a corrupt-but-readable file on disk was
    previously trusted blindly). MP4/ISO-BMFF files start with a size field
    followed by one of a handful of box-type fourCCs (most commonly
    b"ftyp"); truncated or corrupted output won't have this structure."""
    if len(content) < 12:
        return False
    box_type = content[4:8]
    return box_type in (b"ftyp", b"moov", b"free", b"mdat", b"wide")


def _read_local_reel(*, settings: Settings, banner: str, card_code: str, cache_version: str) -> bytes | None:
    path = _local_reel_path(settings=settings, banner=banner, card_code=card_code, cache_version=cache_version)
    try:
        content = path.read_bytes()
    except (FileNotFoundError, OSError):
        return None
    if not _is_valid_mp4(content):
        return None
    return content


def _write_local_reel_atomic(*, settings: Settings, banner: str, card_code: str, cache_version: str, content: bytes) -> None:
    """Write-to-temp-then-rename so a crash mid-write can never leave a
    truncated/corrupt file at the real path — os.replace (via Path.replace)
    is atomic on POSIX. Also drops any other *.mp4 in this card's directory
    (necessarily a stale version — this is the only writer), so tuning the
    renderer repeatedly doesn't leave dead generations on disk forever."""
    path = _local_reel_path(settings=settings, banner=banner, card_code=card_code, cache_version=cache_version)
    path.parent.mkdir(parents=True, exist_ok=True)
    for stale in path.parent.glob("*.mp4"):
        if stale != path:
            stale.unlink(missing_ok=True)
    tmp_path = path.with_name(path.name + ".tmp")
    tmp_path.write_bytes(content)
    tmp_path.replace(path)


@dataclass(slots=True, frozen=True)
class ResolvedReelAnimation:
    payload: str | BufferedInputFile
    needs_caching: bool
    cache_version: str


class GachaRenderBusyError(RuntimeError):
    """Another reel render still holds the slot, so this pull goes out without an animation instead of waiting."""


async def _acquire_render_slot(*, wait: bool) -> None:
    if not wait:
        if not _render_slot.acquire(blocking=False):
            raise GachaRenderBusyError("a gacha reel render is already running")
        return
    while not _render_slot.acquire(blocking=False):
        await asyncio.sleep(_RENDER_SLOT_POLL_SECONDS)


def _render_reel_in_worker(*, build_kwargs: dict) -> bytes:
    try:
        return build_gacha_reel_animation(**build_kwargs)
    finally:
        _render_slot.release()


async def _render_reel_gif(
    *,
    settings: Settings,
    banner: str,
    landing_card_code: str,
    landing_card_name: str,
    landing_rarity_label: str,
    landing_border_color: tuple[int, int, int],
    wait_for_slot: bool = False,
) -> bytes:
    catalog = await _get_banner_catalog(settings, banner)
    filler_pool = [card for card in catalog.cards if card.code != landing_card_code] or list(catalog.cards)
    if not filler_pool:
        raise GachaUseCaseError(f"У баннера {banner} нет карт для анимации.")
    chosen = random.sample(filler_pool, k=min(_FILLER_COUNT, len(filler_pool)))

    landing_meta = next((card for card in catalog.cards if card.code == landing_card_code), None)
    landing_image_url = landing_meta.image_url if landing_meta is not None else None
    if landing_image_url is None:
        raise GachaUseCaseError(f"Карта {landing_card_code} не найдена в каталоге баннера {banner}.")

    async with httpx.AsyncClient(timeout=settings.gacha_timeout_seconds, follow_redirects=True) as client:
        filler_bytes, landing_image_bytes = await asyncio.gather(
            asyncio.gather(*(_fetch_bytes_cached(client, card.image_url) for card in chosen)),
            _fetch_bytes_cached(client, landing_image_url),
        )
    filler_cards = [
        ReelCard(
            name=card.name,
            rarity_label=card.rarity_label,
            border_color=_rarity_border_color(card.rarity),
            image_bytes=image_bytes,
        )
        for card, image_bytes in zip(chosen, filler_bytes, strict=True)
    ]

    landing_card = ReelCard(
        name=landing_card_name,
        rarity_label=landing_rarity_label,
        border_color=landing_border_color,
        image_bytes=landing_image_bytes,
    )
    # Pillow rendering is synchronous CPU-bound work (~4-10s) — calling it
    # directly here would freeze the whole bot's event loop for every user
    # for the entire render, not just this one card. It runs in a worker
    # thread of its own, which also releases the render slot when it ends.
    build_kwargs = {
        "background_bytes": _background_path_for_banner(banner).read_bytes(),
        "landing_card": landing_card,
        "filler_cards": filler_cards,
    }
    await _acquire_render_slot(wait=wait_for_slot)
    try:
        future = asyncio.get_running_loop().run_in_executor(
            _RENDER_EXECUTOR, partial(_render_reel_in_worker, build_kwargs=build_kwargs)
        )
    except BaseException:
        _render_slot.release()
        raise
    return await future


async def resolve_gacha_reel_animation(
    *,
    settings: Settings,
    activity_repo,
    banner: str,
    landing_card_code: str,
    landing_card_name: str,
    landing_rarity: str,
    landing_rarity_label: str,
    skip_cached_lookup: bool = False,
    wait_for_render_slot: bool = False,
) -> ResolvedReelAnimation:
    """Three tiers, cheapest first: Telegram `file_id` (Postgres, решение 9's
    hybrid cache) → local GIF on disk (survives restarts, avoids a re-render
    if a stored file_id ever goes bad) → full Pillow render. `skip_cached_lookup`
    bypasses tier 1 — used when a caller already tried a stored file_id and
    Telegram rejected it, so retrying the same file_id would just fail again.
    A render only starts when no other render is running: user pulls pass
    `wait_for_render_slot=False` and get `GachaRenderBusyError` instead of
    queueing; the warmup waits for its turn.
    Caching the *result* of a successful send (a new file_id) is the
    caller's job via `cache_reel_variant_after_send`, not this function's —
    this function only decides what to try sending."""
    cache_version = await _compute_landing_cache_version(settings, banner=banner, landing_card_code=landing_card_code)

    if not skip_cached_lookup:
        variant_count = await activity_repo.get_gacha_animation_variant_count(
            banner=banner, card_code=landing_card_code, cache_version=cache_version
        )
        should_generate = variant_count < _VARIANT_CAP or random.random() < _REGENERATE_PROBABILITY

        if not should_generate:
            cached_file_id = await activity_repo.get_random_gacha_animation_variant_file_id(
                banner=banner, card_code=landing_card_code, cache_version=cache_version
            )
            if cached_file_id is not None:
                logger.info(
                    "Gacha reel resolved: tier=file_id banner=%s card=%s version=%s",
                    banner, landing_card_code, cache_version,
                )
                return ResolvedReelAnimation(payload=cached_file_id, needs_caching=False, cache_version=cache_version)

    local_bytes = _read_local_reel(settings=settings, banner=banner, card_code=landing_card_code, cache_version=cache_version)
    if local_bytes is not None:
        logger.info(
            "Gacha reel resolved: tier=local_disk banner=%s card=%s version=%s",
            banner, landing_card_code, cache_version,
        )
        filename = f"gacha_reel_{banner}_{landing_card_code}.mp4"
        return ResolvedReelAnimation(
            payload=BufferedInputFile(local_bytes, filename=filename), needs_caching=True, cache_version=cache_version
        )

    logger.info(
        "Gacha reel resolved: tier=render banner=%s card=%s version=%s",
        banner, landing_card_code, cache_version,
    )
    mp4_bytes = await _render_reel_gif(
        settings=settings,
        banner=banner,
        landing_card_code=landing_card_code,
        landing_card_name=landing_card_name,
        landing_rarity_label=landing_rarity_label,
        landing_border_color=_rarity_border_color(landing_rarity),
        wait_for_slot=wait_for_render_slot,
    )
    _write_local_reel_atomic(
        settings=settings, banner=banner, card_code=landing_card_code, cache_version=cache_version, content=mp4_bytes
    )
    filename = f"gacha_reel_{banner}_{landing_card_code}.mp4"
    return ResolvedReelAnimation(
        payload=BufferedInputFile(mp4_bytes, filename=filename), needs_caching=True, cache_version=cache_version
    )


async def cache_reel_variant_after_send(
    *, activity_repo, banner: str, card_code: str, telegram_file_id: str, cache_version: str
) -> None:
    count = await activity_repo.get_gacha_animation_variant_count(banner=banner, card_code=card_code, cache_version=cache_version)
    if count >= _VARIANT_CAP:
        await activity_repo.evict_oldest_gacha_animation_variant(banner=banner, card_code=card_code)
    await activity_repo.add_gacha_animation_variant(
        banner=banner, card_code=card_code, telegram_file_id=telegram_file_id, cache_version=cache_version
    )


_ready_animation_banners: set[str] = set()


async def is_gacha_animation_cache_ready(settings: Settings, activity_repo, banner: str | None = None) -> bool:
    """Coarse readiness gate for the 'preparing' message — not a precise
    per-card version check (that already happens for real inside
    `resolve_gacha_reel_animation` on every pull). Just: does every card in
    the given banner (or in every active banner when `banner` is None) have
    *some* cached variant at all? Readiness is per banner, so a cold banner
    such as ZZZ never switches animations of already-warm Genshin/HSR off.
    Sticky per banner once true: evict-then-add (решение 9) never drops a
    card back to zero variants, so readiness can only go False → True."""
    banners = _ACTIVE_BANNERS if banner is None else (banner,)
    for current in banners:
        if current in _ready_animation_banners:
            continue
        catalog = await _get_banner_catalog(settings, current)
        cached_versions = await activity_repo.get_gacha_animation_cached_versions_by_card(banner=current)
        if any(card.code not in cached_versions for card in catalog.cards):
            return False
        _ready_animation_banners.add(current)
    return True


async def warm_up_gacha_animation_cache(
    *, settings: Settings, bot: Bot, activity_repo, on_card_done=None, on_card_error=None
) -> None:
    """Fire-and-forget background warmup (see docs/GACHA_MODERNIZATION_TODO.md,
    раздел 10): ensures at least one cached variant per card across active
    banners, so real users don't hit a live ~4-5s render. Runs to Telegram
    via the admin's own DM (send, capture file_id, delete — same pattern as
    a real pull's animation) since minting a file_id requires an actual
    send. Never raises — a single card failing must not abort the rest,
    and the caller (bot startup) must never crash because of this task."""
    if settings.gacha_admin_user_id is None:
        logger.warning("Gacha animation warmup skipped: GACHA_ADMIN_USER_ID is not configured.")
        return

    for banner in _ACTIVE_BANNERS:
        try:
            catalog = await _get_banner_catalog(settings, banner)
            cached_versions = await activity_repo.get_gacha_animation_cached_versions_by_card(banner=banner)
        except Exception:
            logger.warning("Gacha animation warmup: failed to load catalog for %s", banner, exc_info=True)
            continue

        for card in catalog.cards:
            if card.code in cached_versions:
                continue
            try:
                await asyncio.wait_for(
                    _warm_up_one_card(
                        settings=settings,
                        bot=bot,
                        activity_repo=activity_repo,
                        banner=banner,
                        card=card,
                        on_card_done=on_card_done,
                    ),
                    timeout=_WARMUP_CARD_TIMEOUT_SECONDS,
                )
            except TimeoutError:
                logger.warning(
                    "Gacha animation warmup timed out after %.0fs for %s/%s, skipping",
                    _WARMUP_CARD_TIMEOUT_SECONDS, banner, card.code,
                )
                if on_card_error is not None:
                    await _safe_on_card_error(on_card_error, banner=banner, card_code=card.code)
                continue
            except Exception:
                logger.warning("Gacha animation warmup failed for %s/%s", banner, card.code, exc_info=True)
                if on_card_error is not None:
                    # A failed card may have left a half-executed DB write
                    # (e.g. add_gacha_animation_variant raised mid-INSERT).
                    # On Postgres, an aborted transaction poisons every
                    # further operation on this session until rolled back —
                    # without this, every card after the first failure
                    # would silently fail to commit for the rest of the run
                    # (found live, 2026-08-19).
                    await _safe_on_card_error(on_card_error, banner=banner, card_code=card.code)
                continue


async def _safe_on_card_error(on_card_error, *, banner: str, card_code: str) -> None:
    """The rollback callback itself can raise (e.g. the underlying DB
    connection was already dropped) — that must not crash the rest of the
    warmup run, or a single flaky connection turns one failed card into a
    total loss of progress for every card after it."""
    try:
        await on_card_error()
    except Exception:
        logger.warning("Gacha animation warmup: rollback after failure also failed for %s/%s", banner, card_code, exc_info=True)


async def _warm_up_one_card(*, settings: Settings, bot: Bot, activity_repo, banner: str, card, on_card_done) -> None:
    resolved = await resolve_gacha_reel_animation(
        settings=settings,
        activity_repo=activity_repo,
        banner=banner,
        landing_card_code=card.code,
        landing_card_name=card.name,
        landing_rarity=card.rarity,
        landing_rarity_label=card.rarity_label,
        wait_for_render_slot=True,
    )
    sent = await bot.send_animation(chat_id=settings.gacha_admin_user_id, animation=resolved.payload)
    if resolved.needs_caching and getattr(sent, "animation", None) is not None:
        await cache_reel_variant_after_send(
            activity_repo=activity_repo,
            banner=banner,
            card_code=card.code,
            telegram_file_id=sent.animation.file_id,
            cache_version=resolved.cache_version,
        )
    try:
        await bot.delete_message(chat_id=settings.gacha_admin_user_id, message_id=sent.message_id)
    except TelegramBadRequest:
        pass
    if on_card_done is not None:
        # Commit incrementally — a restart mid-warmup must not throw away
        # already-processed cards. The expensive part (Pillow render) is
        # separately safe on local disk regardless, but without this a
        # restart still re-uploads/re-sends every card processed so far.
        await on_card_done()
