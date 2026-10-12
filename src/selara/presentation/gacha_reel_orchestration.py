from __future__ import annotations

import asyncio
import hashlib
import logging
import random
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
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

# Warmup budgets, one per stage of a card. They used to share one 60 s ceiling, so a long wait for the render slot ate the
# time the render itself needed. A card that runs out of slot or render time is deferred and tried once more after the
# rest of the catalog; a card whose upload runs out of time is deferred the same way.
_WARMUP_SLOT_WAIT_SECONDS = 120.0
_WARMUP_RENDER_SECONDS = 60.0
_WARMUP_UPLOAD_SECONDS = 60.0
# Hard ceiling on one card attempt. It covers the three stages above and the catalog and image fetches, which have no
# budget of their own. Without it, a hung network call (e.g. a dead-but-not-closed connection to Telegram) blocks the
# whole sequential warmup loop forever with no exception and no log line (found live, 2026-08-19: warmup stalled on one
# card for 4+ hours with zero further progress or errors).
_WARMUP_CARD_TIMEOUT_SECONDS = 300.0

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


# The cache version hashes this frozen copy of the renderer, not the live gacha_reel.py, because the copy is the file the
# cached MP4s were rendered with (commit b4cb08e). The live renderer now streams frames to ffmpeg, so its bytes changed,
# but it draws the same frames: tests/unit/test_gacha_reel.py checks that pixel by pixel. Changing what is drawn means
# adding a new frozen copy here, which changes the version of every card at once.
_VISUAL_RENDERER_SOURCE = Path(__file__).resolve().parent / "gacha_reel_visual_v1.py.txt"


def _compute_cache_version(*, banner: str, landing_card_etag: str) -> str:
    """Deterministic content hash — never a manually-bumped number. Changing
    the frozen renderer copy or the background asset changes this for *every*
    card at once; changing only one card's source portrait (its ETag from the
    gacha service) changes it only for that card. See
    docs/GACHA_MODERNIZATION_TODO.md, раздел 10."""
    renderer_source = _VISUAL_RENDERER_SOURCE.read_bytes()
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


async def _acquire_render_slot(*, wait: bool, timeout: float | None = None) -> None:
    if not wait:
        if not _render_slot.acquire(blocking=False):
            raise GachaRenderBusyError("a gacha reel render is already running")
        return
    deadline = None if timeout is None else time.monotonic() + timeout
    while not _render_slot.acquire(blocking=False):
        if deadline is not None and time.monotonic() >= deadline:
            raise GachaRenderBusyError("gave up waiting for the gacha reel render slot")
        await asyncio.sleep(_RENDER_SLOT_POLL_SECONDS)


def _render_reel_in_worker(
    *, build_kwargs: dict, settings: Settings, banner: str, card_code: str, cache_version: str
) -> bytes:
    """Runs in `_RENDER_EXECUTOR`. The MP4 goes to the local disk tier from this thread as well, so a render that a
    timeout has abandoned is not thrown away: it is picked up from disk on the next attempt."""
    try:
        started = time.monotonic()
        content = build_gacha_reel_animation(**build_kwargs)
        rendered = time.monotonic()
        _write_local_reel_atomic(
            settings=settings, banner=banner, card_code=card_code, cache_version=cache_version, content=content
        )
        logger.info(
            "Gacha reel rendered: banner=%s card=%s version=%s render_seconds=%.1f write_seconds=%.2f",
            banner, card_code, cache_version, rendered - started, time.monotonic() - rendered,
        )
        return content
    finally:
        _render_slot.release()


def _release_slot_if_cancelled(job: Future) -> None:
    """A job cancelled before a worker picks it up never reaches the `finally` above, so the slot would stay taken."""
    if job.cancelled():
        _render_slot.release()


async def _render_reel_gif(
    *,
    settings: Settings,
    banner: str,
    landing_card_code: str,
    landing_card_name: str,
    landing_rarity_label: str,
    landing_border_color: tuple[int, int, int],
    cache_version: str,
    wait_for_slot: bool = False,
    slot_timeout: float | None = None,
    render_timeout: float | None = None,
) -> bytes:
    """`slot_timeout` bounds the wait for the render slot (GachaRenderBusyError when it passes). `render_timeout` bounds
    the render itself. A render cut off by it keeps running in its thread and still writes its MP4 to disk."""
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
    await _acquire_render_slot(wait=wait_for_slot, timeout=slot_timeout)
    try:
        job = _RENDER_EXECUTOR.submit(
            partial(
                _render_reel_in_worker,
                build_kwargs=build_kwargs,
                settings=settings,
                banner=banner,
                card_code=landing_card_code,
                cache_version=cache_version,
            ),
        )
    except BaseException:
        _render_slot.release()
        raise
    job.add_done_callback(_release_slot_if_cancelled)
    rendering = asyncio.wrap_future(job)
    if render_timeout is None:
        return await rendering
    return await asyncio.wait_for(rendering, timeout=render_timeout)


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
    allow_render: bool = True,
    wait_for_render_slot: bool = False,
    render_slot_timeout: float | None = None,
    render_timeout: float | None = None,
    release_connection=None,
) -> ResolvedReelAnimation | None:
    """Three tiers, cheapest first: Telegram `file_id` (Postgres, решение 9's
    hybrid cache) → local MP4 on disk (survives restarts, avoids a re-render
    if a stored file_id ever goes bad) → full Pillow render. `skip_cached_lookup`
    bypasses tier 1 — used when a caller already tried a stored file_id and
    Telegram rejected it, so retrying the same file_id would just fail again.
    With `allow_render=False` (user pulls) the render tier never runs: the result
    is None when this card has neither a usable file_id nor a disk copy, and the
    caller renders it in the background with `ensure_gacha_reel_on_disk`.
    Otherwise a render waits for the slot only when `wait_for_render_slot` is set,
    gives up waiting after `render_slot_timeout` and is cut off after
    `render_timeout`. `release_connection` is awaited right before a render, so
    the caller's pooled connection is not held through the render.
    Caching the *result* of a successful send (a new file_id) is the
    caller's job via `cache_reel_variant_after_send`, not this function's —
    this function only decides what to try sending."""
    cache_version = await _compute_landing_cache_version(settings, banner=banner, landing_card_code=landing_card_code)

    variant_count = 0
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

    if not allow_render:
        # No disk copy, so a variant already cached for this version is the only animation there is.
        cached_file_id = None
        if variant_count > 0:
            cached_file_id = await activity_repo.get_random_gacha_animation_variant_file_id(
                banner=banner, card_code=landing_card_code, cache_version=cache_version
            )
        if cached_file_id is None:
            logger.info(
                "Gacha reel not cached yet: banner=%s card=%s version=%s",
                banner, landing_card_code, cache_version,
            )
            return None
        logger.info(
            "Gacha reel resolved: tier=file_id banner=%s card=%s version=%s",
            banner, landing_card_code, cache_version,
        )
        return ResolvedReelAnimation(payload=cached_file_id, needs_caching=False, cache_version=cache_version)

    logger.info(
        "Gacha reel resolved: tier=render banner=%s card=%s version=%s",
        banner, landing_card_code, cache_version,
    )
    if release_connection is not None:
        await release_connection()
    mp4_bytes = await _render_reel_gif(
        settings=settings,
        banner=banner,
        landing_card_code=landing_card_code,
        landing_card_name=landing_card_name,
        landing_rarity_label=landing_rarity_label,
        landing_border_color=_rarity_border_color(landing_rarity),
        cache_version=cache_version,
        wait_for_slot=wait_for_render_slot,
        slot_timeout=render_slot_timeout,
        render_timeout=render_timeout,
    )
    filename = f"gacha_reel_{banner}_{landing_card_code}.mp4"
    return ResolvedReelAnimation(
        payload=BufferedInputFile(mp4_bytes, filename=filename), needs_caching=True, cache_version=cache_version
    )


_reels_being_rendered: set[tuple[str, str, str]] = set()


async def ensure_gacha_reel_on_disk(
    *,
    settings: Settings,
    banner: str,
    landing_card_code: str,
    landing_card_name: str,
    landing_rarity: str,
    landing_rarity_label: str,
) -> None:
    """Background task of a user pull: renders this card's MP4 for the current renderer version when no copy is on disk,
    so that a later pull can send it from disk and cache its file_id. It never waits for the render slot. While another
    render holds the slot, the card is left for a later pull. Never raises."""
    try:
        cache_version = await _compute_landing_cache_version(settings, banner=banner, landing_card_code=landing_card_code)
    except Exception:
        logger.warning(
            "Gacha reel not rendered: cannot compute cache version for %s/%s", banner, landing_card_code, exc_info=True
        )
        return
    local_bytes = _read_local_reel(settings=settings, banner=banner, card_code=landing_card_code, cache_version=cache_version)
    if local_bytes is not None:
        return
    key = (banner, landing_card_code, cache_version)
    if key in _reels_being_rendered:
        return
    _reels_being_rendered.add(key)
    try:
        await _render_reel_gif(
            settings=settings,
            banner=banner,
            landing_card_code=landing_card_code,
            landing_card_name=landing_card_name,
            landing_rarity_label=landing_rarity_label,
            landing_border_color=_rarity_border_color(landing_rarity),
            cache_version=cache_version,
            wait_for_slot=False,
        )
    except GachaRenderBusyError:
        logger.info("Gacha reel render for %s/%s waits: another render holds the slot", banner, landing_card_code)
    except Exception:
        logger.warning("Gacha reel background render failed for %s/%s", banner, landing_card_code, exc_info=True)
    finally:
        _reels_being_rendered.discard(key)


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
    *, settings: Settings, bot: Bot, activity_repo, on_card_done=None, on_card_error=None, release_connection=None
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

    deferred: list[tuple[str, object]] = []
    for banner in _ACTIVE_BANNERS:
        try:
            catalog = await _get_banner_catalog(settings, banner)
            cached_versions = await activity_repo.get_gacha_animation_cached_versions_by_card(banner=banner)
        except Exception:
            logger.warning("Gacha animation warmup: failed to load catalog for %s", banner, exc_info=True)
            continue
        if release_connection is not None:
            # Ends the read-only transaction now, before the per-card version checks make network calls.
            await release_connection()

        for card in catalog.cards:
            try:
                current_version = await _compute_landing_cache_version(
                    settings, banner=banner, landing_card_code=card.code
                )
            except Exception:
                logger.warning("Gacha animation warmup: cannot compute cache version for %s/%s", banner, card.code, exc_info=True)
                continue
            # Compare with the version the renderer produces today, not with "any version": after a renderer change the
            # old variants are stale, and a user pull would otherwise render live inside its handler.
            if current_version in cached_versions.get(card.code, set()):
                continue
            if await _warm_up_card_once(
                settings=settings,
                bot=bot,
                activity_repo=activity_repo,
                banner=banner,
                card=card,
                on_card_done=on_card_done,
                on_card_error=on_card_error,
                release_connection=release_connection,
            ):
                deferred.append((banner, card))

    # The cards deferred for lack of slot or render time get one pass of their own, after the rest of the catalog.
    for banner, card in deferred:
        if await _warm_up_card_once(
            settings=settings,
            bot=bot,
            activity_repo=activity_repo,
            banner=banner,
            card=card,
            on_card_done=on_card_done,
            on_card_error=on_card_error,
            release_connection=release_connection,
        ):
            logger.warning("Gacha animation warmup left %s/%s for the next start", banner, card.code)


async def _warm_up_card_once(
    *, settings: Settings, bot: Bot, activity_repo, banner: str, card, on_card_done, on_card_error, release_connection
) -> bool:
    """One attempt at one card. Returns True when the attempt was deferred, because the render slot or the render ran out
    of budget or the upload did, so the caller can try the card again later. Other failures are logged and skipped."""
    try:
        await asyncio.wait_for(
            _warm_up_one_card(
                settings=settings,
                bot=bot,
                activity_repo=activity_repo,
                banner=banner,
                card=card,
                on_card_done=on_card_done,
                release_connection=release_connection,
            ),
            timeout=_WARMUP_CARD_TIMEOUT_SECONDS,
        )
    except (TimeoutError, GachaRenderBusyError):
        logger.warning("Gacha animation warmup deferred %s/%s: its time budget ran out", banner, card.code)
        if on_card_error is not None:
            await _safe_on_card_error(on_card_error, banner=banner, card_code=card.code)
        return True
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
    return False


async def _safe_on_card_error(on_card_error, *, banner: str, card_code: str) -> None:
    """The rollback callback itself can raise (e.g. the underlying DB
    connection was already dropped) — that must not crash the rest of the
    warmup run, or a single flaky connection turns one failed card into a
    total loss of progress for every card after it."""
    try:
        await on_card_error()
    except Exception:
        logger.warning("Gacha animation warmup: rollback after failure also failed for %s/%s", banner, card_code, exc_info=True)


async def _warm_up_one_card(
    *, settings: Settings, bot: Bot, activity_repo, banner: str, card, on_card_done, release_connection=None
) -> None:
    resolved = await resolve_gacha_reel_animation(
        settings=settings,
        activity_repo=activity_repo,
        banner=banner,
        landing_card_code=card.code,
        landing_card_name=card.name,
        landing_rarity=card.rarity,
        landing_rarity_label=card.rarity_label,
        wait_for_render_slot=True,
        render_slot_timeout=_WARMUP_SLOT_WAIT_SECONDS,
        render_timeout=_WARMUP_RENDER_SECONDS,
        release_connection=release_connection,
    )
    await asyncio.wait_for(
        _upload_warmup_animation(
            settings=settings,
            bot=bot,
            activity_repo=activity_repo,
            banner=banner,
            card=card,
            resolved=resolved,
        ),
        timeout=_WARMUP_UPLOAD_SECONDS,
    )
    if on_card_done is not None:
        # Commit incrementally — a restart mid-warmup must not throw away
        # already-processed cards. The expensive part (Pillow render) is
        # separately safe on local disk regardless, but without this a
        # restart still re-uploads/re-sends every card processed so far.
        await on_card_done()


async def _upload_warmup_animation(*, settings: Settings, bot: Bot, activity_repo, banner: str, card, resolved) -> None:
    """Sends the card's animation to the admin chat, caches the resulting file_id and deletes the message again."""
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
