from __future__ import annotations

import asyncio
import hashlib
import threading
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from aiogram.types import BufferedInputFile

from selara.presentation import gacha_reel_orchestration as orch


def _valid_mp4_bytes() -> bytes:
    """A minimal structurally-valid MP4 (ISO-BMFF ftyp box header) —
    _read_local_reel validates structure (pre-deploy audit finding,
    2026-08-19; extended to MP4 on the GIF->MP4 switch), so plain
    placeholder bytes like b"gif-bytes" no longer round-trip through the
    local-disk tier. Name kept for now to minimize test-file churn — the
    content itself is what changed."""
    return b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isomiso2mp41" + b"\x00" * 16


@pytest.fixture(autouse=True)
def _stable_landing_cache_version(monkeypatch: pytest.MonkeyPatch) -> None:
    """The warmup compares each card's variants with the version the renderer
    produces now. Tests run against "v1" unless they patch it themselves."""
    monkeypatch.setattr(orch, "_compute_landing_cache_version", AsyncMock(return_value="v1"))


@pytest.mark.asyncio
async def test_resolve_reuses_cached_variant_when_under_regenerate_roll(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setattr(orch, "_compute_landing_cache_version", AsyncMock(return_value="v1"))
    activity_repo = SimpleNamespace(
        get_gacha_animation_variant_count=AsyncMock(return_value=3),
        get_random_gacha_animation_variant_file_id=AsyncMock(return_value="cached-file-id"),
    )
    monkeypatch.setattr(orch.random, "random", lambda: 0.99)  # above regenerate probability
    render_mock = AsyncMock()
    monkeypatch.setattr(orch, "_render_reel_gif", render_mock)

    result = await orch.resolve_gacha_reel_animation(
        settings=SimpleNamespace(gacha_reel_cache_dir=str(tmp_path)),
        activity_repo=activity_repo,
        banner="genshin",
        landing_card_code="furina",
        landing_card_name="Фурина",
        landing_rarity="mythic",
        landing_rarity_label="🟥 Мифическая",
    )

    assert result.payload == "cached-file-id"
    assert result.needs_caching is False
    assert result.cache_version == "v1"
    render_mock.assert_not_awaited()
    activity_repo.get_gacha_animation_variant_count.assert_awaited_once_with(
        banner="genshin", card_code="furina", cache_version="v1"
    )
    activity_repo.get_random_gacha_animation_variant_file_id.assert_awaited_once_with(
        banner="genshin", card_code="furina", cache_version="v1"
    )


@pytest.mark.asyncio
async def test_resolve_generates_new_variant_when_under_cap(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setattr(orch, "_compute_landing_cache_version", AsyncMock(return_value="v1"))
    activity_repo = SimpleNamespace(
        get_gacha_animation_variant_count=AsyncMock(return_value=1),
        get_random_gacha_animation_variant_file_id=AsyncMock(),
    )
    gif_bytes = _valid_mp4_bytes()
    render_mock = AsyncMock(return_value=gif_bytes)
    monkeypatch.setattr(orch, "_render_reel_gif", render_mock)

    result = await orch.resolve_gacha_reel_animation(
        settings=SimpleNamespace(gacha_reel_cache_dir=str(tmp_path)),
        activity_repo=activity_repo,
        banner="genshin",
        landing_card_code="furina",
        landing_card_name="Фурина",
        landing_rarity="mythic",
        landing_rarity_label="🟥 Мифическая",
    )

    assert isinstance(result.payload, BufferedInputFile)
    assert result.payload.data == gif_bytes
    assert result.payload.filename.endswith(".mp4")  # regression guard: found live 2026-08-19 —
    # a mismatched .gif filename on real MP4 bytes made Telegram silently
    # refuse to classify the upload as an animation (no .animation in the
    # response), so the variant was never cached and it displayed as a
    # generic file instead of inline media.
    assert result.needs_caching is True
    assert result.cache_version == "v1"
    render_mock.assert_awaited_once()
    activity_repo.get_random_gacha_animation_variant_file_id.assert_not_awaited()
    assert render_mock.await_args.kwargs["cache_version"] == "v1"


@pytest.mark.asyncio
async def test_resolve_generates_when_regenerate_roll_hits_despite_cap(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setattr(orch, "_compute_landing_cache_version", AsyncMock(return_value="v1"))
    activity_repo = SimpleNamespace(
        get_gacha_animation_variant_count=AsyncMock(return_value=3),
        get_random_gacha_animation_variant_file_id=AsyncMock(return_value="cached-file-id"),
    )
    monkeypatch.setattr(orch.random, "random", lambda: 0.01)  # below regenerate probability
    render_mock = AsyncMock(return_value=b"gif-bytes")
    monkeypatch.setattr(orch, "_render_reel_gif", render_mock)

    result = await orch.resolve_gacha_reel_animation(
        settings=SimpleNamespace(gacha_reel_cache_dir=str(tmp_path)),
        activity_repo=activity_repo,
        banner="genshin",
        landing_card_code="furina",
        landing_card_name="Фурина",
        landing_rarity="mythic",
        landing_rarity_label="🟥 Мифическая",
    )

    assert result.needs_caching is True
    render_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_resolve_falls_back_to_generation_if_cache_race_leaves_no_variant(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """If variant_count reported >= cap but the random pick still returns
    None (e.g. a concurrent eviction), generation must not be skipped —
    the user must never end up with no animation to send."""
    monkeypatch.setattr(orch, "_compute_landing_cache_version", AsyncMock(return_value="v1"))
    activity_repo = SimpleNamespace(
        get_gacha_animation_variant_count=AsyncMock(return_value=3),
        get_random_gacha_animation_variant_file_id=AsyncMock(return_value=None),
    )
    monkeypatch.setattr(orch.random, "random", lambda: 0.99)
    render_mock = AsyncMock(return_value=b"gif-bytes")
    monkeypatch.setattr(orch, "_render_reel_gif", render_mock)

    result = await orch.resolve_gacha_reel_animation(
        settings=SimpleNamespace(gacha_reel_cache_dir=str(tmp_path)),
        activity_repo=activity_repo,
        banner="genshin",
        landing_card_code="furina",
        landing_card_name="Фурина",
        landing_rarity="mythic",
        landing_rarity_label="🟥 Мифическая",
    )

    assert result.needs_caching is True
    render_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_resolve_uses_local_disk_tier_before_rendering(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """Tier 2: if the DB has no valid file_id (cache miss/cold cache) but a
    matching-version GIF already exists on local disk (e.g. survived a
    Telegram file_id going stale, or a container recreate), reuse it
    instead of paying for a full Pillow re-render."""
    settings = SimpleNamespace(gacha_reel_cache_dir=str(tmp_path))
    monkeypatch.setattr(orch, "_compute_landing_cache_version", AsyncMock(return_value="v1"))
    gif_bytes = _valid_mp4_bytes()
    orch._write_local_reel_atomic(settings=settings, banner="genshin", card_code="furina", cache_version="v1", content=gif_bytes)
    activity_repo = SimpleNamespace(
        get_gacha_animation_variant_count=AsyncMock(return_value=0),
        get_random_gacha_animation_variant_file_id=AsyncMock(),
    )
    render_mock = AsyncMock()
    monkeypatch.setattr(orch, "_render_reel_gif", render_mock)

    result = await orch.resolve_gacha_reel_animation(
        settings=settings,
        activity_repo=activity_repo,
        banner="genshin",
        landing_card_code="furina",
        landing_card_name="Фурина",
        landing_rarity="mythic",
        landing_rarity_label="🟥 Мифическая",
    )

    assert isinstance(result.payload, BufferedInputFile)
    assert result.payload.data == gif_bytes
    assert result.payload.filename.endswith(".mp4")
    assert result.needs_caching is True  # still needs its file_id cached in Postgres
    render_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_resolve_skip_cached_lookup_forces_local_or_render_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """Used to recover when a stored Telegram file_id actually fails to
    send — retrying with the same file_id would just fail again."""
    monkeypatch.setattr(orch, "_compute_landing_cache_version", AsyncMock(return_value="v1"))
    activity_repo = SimpleNamespace(
        get_gacha_animation_variant_count=AsyncMock(return_value=3),
        get_random_gacha_animation_variant_file_id=AsyncMock(return_value="stale-file-id"),
    )
    render_mock = AsyncMock(return_value=b"gif-bytes")
    monkeypatch.setattr(orch, "_render_reel_gif", render_mock)

    result = await orch.resolve_gacha_reel_animation(
        settings=SimpleNamespace(gacha_reel_cache_dir=str(tmp_path)),
        activity_repo=activity_repo,
        banner="genshin",
        landing_card_code="furina",
        landing_card_name="Фурина",
        landing_rarity="mythic",
        landing_rarity_label="🟥 Мифическая",
        skip_cached_lookup=True,
    )

    assert isinstance(result.payload, BufferedInputFile)
    render_mock.assert_awaited_once()
    activity_repo.get_random_gacha_animation_variant_file_id.assert_not_awaited()


@pytest.mark.asyncio
async def test_cache_reel_variant_evicts_oldest_when_at_cap() -> None:
    activity_repo = SimpleNamespace(
        get_gacha_animation_variant_count=AsyncMock(return_value=3),
        evict_oldest_gacha_animation_variant=AsyncMock(),
        add_gacha_animation_variant=AsyncMock(),
    )

    await orch.cache_reel_variant_after_send(
        activity_repo=activity_repo, banner="genshin", card_code="furina", telegram_file_id="new-id", cache_version="v1"
    )

    activity_repo.evict_oldest_gacha_animation_variant.assert_awaited_once_with(banner="genshin", card_code="furina")
    activity_repo.add_gacha_animation_variant.assert_awaited_once_with(
        banner="genshin", card_code="furina", telegram_file_id="new-id", cache_version="v1"
    )


@pytest.mark.asyncio
async def test_cache_reel_variant_does_not_evict_when_under_cap() -> None:
    activity_repo = SimpleNamespace(
        get_gacha_animation_variant_count=AsyncMock(return_value=1),
        evict_oldest_gacha_animation_variant=AsyncMock(),
        add_gacha_animation_variant=AsyncMock(),
    )

    await orch.cache_reel_variant_after_send(
        activity_repo=activity_repo, banner="genshin", card_code="furina", telegram_file_id="new-id", cache_version="v1"
    )

    activity_repo.evict_oldest_gacha_animation_variant.assert_not_awaited()
    activity_repo.add_gacha_animation_variant.assert_awaited_once()


def test_write_local_reel_atomic_round_trip_and_no_partial_file_on_crash(tmp_path) -> None:
    settings = SimpleNamespace(gacha_reel_cache_dir=str(tmp_path))
    assert orch._read_local_reel(settings=settings, banner="genshin", card_code="furina", cache_version="v1") is None

    gif_bytes = _valid_mp4_bytes()
    orch._write_local_reel_atomic(settings=settings, banner="genshin", card_code="furina", cache_version="v1", content=gif_bytes)

    assert orch._read_local_reel(settings=settings, banner="genshin", card_code="furina", cache_version="v1") == gif_bytes
    # no stray .tmp file left behind after a successful atomic write
    card_dir = orch._local_reel_path(settings=settings, banner="genshin", card_code="furina", cache_version="v1").parent
    assert list(card_dir.glob("*.tmp")) == []


def test_write_local_reel_atomic_replaces_stale_version_and_cleans_up_old_file(tmp_path) -> None:
    settings = SimpleNamespace(gacha_reel_cache_dir=str(tmp_path))
    old_bytes = _valid_mp4_bytes()
    new_bytes = _valid_mp4_bytes()
    orch._write_local_reel_atomic(settings=settings, banner="genshin", card_code="furina", cache_version="v1", content=old_bytes)
    orch._write_local_reel_atomic(settings=settings, banner="genshin", card_code="furina", cache_version="v2", content=new_bytes)

    assert orch._read_local_reel(settings=settings, banner="genshin", card_code="furina", cache_version="v1") is None
    assert orch._read_local_reel(settings=settings, banner="genshin", card_code="furina", cache_version="v2") == new_bytes


def test_read_local_reel_missing_file_returns_none_not_an_error(tmp_path) -> None:
    settings = SimpleNamespace(gacha_reel_cache_dir=str(tmp_path))
    assert orch._read_local_reel(settings=settings, banner="genshin", card_code="unknown", cache_version="v1") is None


def test_read_local_reel_rejects_corrupted_or_truncated_file(tmp_path) -> None:
    """Pre-deploy audit finding (2026-08-19): atomic writes prevent *new*
    corruption, but don't protect against a file that's already
    truncated/corrupt on disk for any other reason (partial disk write,
    manual tampering, a pre-fix crash). Reading raw bytes without
    validating the GIF structure would hand Telegram a broken file
    silently. Must fall back to Tier 3 (render) instead of trusting it."""
    settings = SimpleNamespace(gacha_reel_cache_dir=str(tmp_path))
    path = orch._local_reel_path(settings=settings, banner="genshin", card_code="furina", cache_version="v1")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"not a real gif, just garbage bytes")

    assert orch._read_local_reel(settings=settings, banner="genshin", card_code="furina", cache_version="v1") is None


def test_read_local_reel_accepts_a_genuine_mp4(tmp_path) -> None:
    valid_mp4_bytes = _valid_mp4_bytes()

    settings = SimpleNamespace(gacha_reel_cache_dir=str(tmp_path))
    orch._write_local_reel_atomic(settings=settings, banner="genshin", card_code="furina", cache_version="v1", content=valid_mp4_bytes)

    result = orch._read_local_reel(settings=settings, banner="genshin", card_code="furina", cache_version="v1")
    assert result == valid_mp4_bytes


def test_compute_cache_version_changes_with_landing_card_etag(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """Deterministic hash-based invalidation (not a manual version number):
    changing the landing card's own portrait ETag changes ONLY that card's
    version, without needing to touch anything else."""
    background = tmp_path / "bg.png"
    background.write_bytes(b"background-bytes")
    monkeypatch.setattr(orch, "_background_path_for_banner", lambda banner: background)

    version_a = orch._compute_cache_version(banner="genshin", landing_card_etag='"etag-a"')
    version_b = orch._compute_cache_version(banner="genshin", landing_card_etag='"etag-b"')
    version_a_again = orch._compute_cache_version(banner="genshin", landing_card_etag='"etag-a"')

    assert version_a != version_b
    assert version_a == version_a_again


_CACHED_VERSIONS_BY_BANNER = {
    "genshin": {"furina": {"v1"}},
    "hsr": {"kafka": {"v1"}},
    "zzz": {"rina": {"v1"}},
}


@pytest.mark.asyncio
async def test_cache_ready_false_when_a_card_has_no_variant_at_all(monkeypatch: pytest.MonkeyPatch) -> None:
    orch._ready_animation_banners.clear()
    catalogs = {
        "genshin": SimpleNamespace(cards=[SimpleNamespace(code="furina"), SimpleNamespace(code="amber")]),
        "hsr": SimpleNamespace(cards=[SimpleNamespace(code="kafka")]),
        "zzz": SimpleNamespace(cards=[SimpleNamespace(code="rina")]),
    }
    monkeypatch.setattr(orch, "_get_banner_catalog", AsyncMock(side_effect=lambda settings, banner: catalogs[banner]))
    activity_repo = SimpleNamespace(
        get_gacha_animation_cached_versions_by_card=AsyncMock(
            side_effect=lambda *, banner: _CACHED_VERSIONS_BY_BANNER[banner]
        )
    )

    result = await orch.is_gacha_animation_cache_ready(SimpleNamespace(), activity_repo)

    assert result is False  # "amber" has no variant at all
    assert orch._ready_animation_banners == set()


@pytest.mark.asyncio
async def test_cache_ready_true_and_sticky_once_every_card_has_a_variant(monkeypatch: pytest.MonkeyPatch) -> None:
    orch._ready_animation_banners.clear()
    catalogs = {
        "genshin": SimpleNamespace(cards=[SimpleNamespace(code="furina")]),
        "hsr": SimpleNamespace(cards=[SimpleNamespace(code="kafka")]),
        "zzz": SimpleNamespace(cards=[SimpleNamespace(code="rina")]),
    }
    get_catalog_mock = AsyncMock(side_effect=lambda settings, banner: catalogs[banner])
    monkeypatch.setattr(orch, "_get_banner_catalog", get_catalog_mock)
    activity_repo = SimpleNamespace(
        get_gacha_animation_cached_versions_by_card=AsyncMock(
            side_effect=lambda *, banner: _CACHED_VERSIONS_BY_BANNER[banner]
        )
    )

    first = await orch.is_gacha_animation_cache_ready(SimpleNamespace(), activity_repo)
    assert first is True
    assert get_catalog_mock.await_count == len(_CACHED_VERSIONS_BY_BANNER)

    # sticky: a second call must not re-check the DB/catalog at all
    second = await orch.is_gacha_animation_cache_ready(SimpleNamespace(), activity_repo)
    assert second is True
    assert get_catalog_mock.await_count == len(_CACHED_VERSIONS_BY_BANNER)


@pytest.mark.asyncio
async def test_cold_zzz_does_not_block_warm_genshin_and_hsr(monkeypatch: pytest.MonkeyPatch) -> None:
    orch._ready_animation_banners.clear()
    catalogs = {
        "genshin": SimpleNamespace(cards=[SimpleNamespace(code="furina")]),
        "hsr": SimpleNamespace(cards=[SimpleNamespace(code="kafka")]),
        "zzz": SimpleNamespace(cards=[SimpleNamespace(code="rina"), SimpleNamespace(code="aria")]),
    }
    monkeypatch.setattr(orch, "_get_banner_catalog", AsyncMock(side_effect=lambda settings, banner: catalogs[banner]))
    activity_repo = SimpleNamespace(
        get_gacha_animation_cached_versions_by_card=AsyncMock(
            side_effect=lambda *, banner: _CACHED_VERSIONS_BY_BANNER[banner]
        )
    )

    # "aria" has no variant yet, so only ZZZ is cold.
    assert await orch.is_gacha_animation_cache_ready(SimpleNamespace(), activity_repo, banner="genshin") is True
    assert await orch.is_gacha_animation_cache_ready(SimpleNamespace(), activity_repo, banner="hsr") is True
    assert await orch.is_gacha_animation_cache_ready(SimpleNamespace(), activity_repo, banner="zzz") is False
    # The global gate used for warmup status still reports not ready while ZZZ is cold.
    assert await orch.is_gacha_animation_cache_ready(SimpleNamespace(), activity_repo) is False
    assert orch._ready_animation_banners == {"genshin", "hsr"}


@pytest.mark.asyncio
async def test_warm_up_generates_only_missing_cards_and_deletes_warmup_message(monkeypatch: pytest.MonkeyPatch) -> None:
    orch._ready_animation_banners.clear()
    catalog = SimpleNamespace(
        cards=[
            SimpleNamespace(code="furina", name="Фурина", rarity="mythic", rarity_label="🟥"),
            SimpleNamespace(code="amber", name="Эмбер", rarity="common", rarity_label="⬜"),
        ]
    )
    monkeypatch.setattr(orch, "_get_banner_catalog", AsyncMock(side_effect=lambda settings, banner: catalog if banner == "genshin" else SimpleNamespace(cards=[])))
    activity_repo = SimpleNamespace(
        get_gacha_animation_cached_versions_by_card=AsyncMock(
            side_effect=lambda *, banner: {"furina": {"v1"}} if banner == "genshin" else {}
        ),
        get_gacha_animation_variant_count=AsyncMock(return_value=0),
        add_gacha_animation_variant=AsyncMock(),
        evict_oldest_gacha_animation_variant=AsyncMock(),
    )
    resolved = SimpleNamespace(payload=BufferedInputFile(b"gif", filename="x.gif"), needs_caching=True, cache_version="v1")
    monkeypatch.setattr(orch, "resolve_gacha_reel_animation", AsyncMock(return_value=resolved))

    sent_message = SimpleNamespace(message_id=42, animation=SimpleNamespace(file_id="warmup-file-id"))
    bot = AsyncMock()
    bot.send_animation = AsyncMock(return_value=sent_message)
    bot.delete_message = AsyncMock()

    settings = SimpleNamespace(gacha_admin_user_id=905302972)
    await orch.warm_up_gacha_animation_cache(settings=settings, bot=bot, activity_repo=activity_repo)

    # only "amber" was missing — furina already had a variant
    bot.send_animation.assert_awaited_once()
    bot.send_animation.assert_awaited_with(chat_id=905302972, animation=resolved.payload)
    bot.delete_message.assert_awaited_once_with(chat_id=905302972, message_id=42)
    activity_repo.add_gacha_animation_variant.assert_awaited_once_with(
        banner="genshin", card_code="amber", telegram_file_id="warmup-file-id", cache_version="v1"
    )


@pytest.mark.asyncio
async def test_warm_up_rerenders_cards_whose_variants_come_from_an_older_renderer_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A variant cached for an older renderer version is stale. Warmup must
    render the card again instead of treating any cached variant as warm,
    otherwise the next user pull renders live inside its handler."""
    orch._ready_animation_banners.clear()
    catalog = SimpleNamespace(cards=[SimpleNamespace(code="furina", name="Фурина", rarity="mythic", rarity_label="🟥")])
    monkeypatch.setattr(
        orch, "_get_banner_catalog", AsyncMock(side_effect=lambda settings, banner: catalog if banner == "genshin" else SimpleNamespace(cards=[]))
    )
    activity_repo = SimpleNamespace(
        get_gacha_animation_cached_versions_by_card=AsyncMock(
            side_effect=lambda *, banner: {"furina": {"v0"}} if banner == "genshin" else {}
        ),
        get_gacha_animation_variant_count=AsyncMock(return_value=0),
        add_gacha_animation_variant=AsyncMock(),
        evict_oldest_gacha_animation_variant=AsyncMock(),
    )
    resolved = SimpleNamespace(payload=BufferedInputFile(b"gif", filename="x.gif"), needs_caching=True, cache_version="v1")
    resolve_mock = AsyncMock(return_value=resolved)
    monkeypatch.setattr(orch, "resolve_gacha_reel_animation", resolve_mock)
    sent_message = SimpleNamespace(message_id=42, animation=SimpleNamespace(file_id="warmup-file-id"))
    bot = AsyncMock()
    bot.send_animation = AsyncMock(return_value=sent_message)
    bot.delete_message = AsyncMock()

    settings = SimpleNamespace(gacha_admin_user_id=905302972)
    await orch.warm_up_gacha_animation_cache(settings=settings, bot=bot, activity_repo=activity_repo)

    resolve_mock.assert_awaited_once()
    assert resolve_mock.await_args.kwargs["wait_for_render_slot"] is True
    activity_repo.add_gacha_animation_variant.assert_awaited_once_with(
        banner="genshin", card_code="furina", telegram_file_id="warmup-file-id", cache_version="v1"
    )


@pytest.mark.asyncio
async def test_warm_up_calls_on_card_done_after_each_successful_card(monkeypatch: pytest.MonkeyPatch) -> None:
    """A restart must not throw away already-processed cards: warmup should
    commit incrementally (per card), not once at the very end — otherwise a
    restart mid-warmup re-does DB work for cards whose (cheap) local-disk
    GIF already exists (see docs/GACHA_MODERNIZATION_TODO.md, раздел 12)."""
    orch._ready_animation_banners.clear()
    catalog = SimpleNamespace(
        cards=[
            SimpleNamespace(code="furina", name="Фурина", rarity="mythic", rarity_label="🟥"),
            SimpleNamespace(code="amber", name="Эмбер", rarity="common", rarity_label="⬜"),
        ]
    )
    monkeypatch.setattr(
        orch, "_get_banner_catalog", AsyncMock(side_effect=lambda settings, banner: catalog if banner == "genshin" else SimpleNamespace(cards=[]))
    )
    activity_repo = SimpleNamespace(
        get_gacha_animation_cached_versions_by_card=AsyncMock(return_value={}),
        get_gacha_animation_variant_count=AsyncMock(return_value=0),
        add_gacha_animation_variant=AsyncMock(),
        evict_oldest_gacha_animation_variant=AsyncMock(),
    )
    resolved = SimpleNamespace(payload=BufferedInputFile(b"gif", filename="x.gif"), needs_caching=True, cache_version="v1")
    monkeypatch.setattr(orch, "resolve_gacha_reel_animation", AsyncMock(return_value=resolved))
    sent_message = SimpleNamespace(message_id=42, animation=SimpleNamespace(file_id="warmup-file-id"))
    bot = AsyncMock()
    bot.send_animation = AsyncMock(return_value=sent_message)
    bot.delete_message = AsyncMock()
    on_card_done = AsyncMock()

    settings = SimpleNamespace(gacha_admin_user_id=905302972)
    await orch.warm_up_gacha_animation_cache(settings=settings, bot=bot, activity_repo=activity_repo, on_card_done=on_card_done)

    assert on_card_done.await_count == 2  # both cards succeeded


@pytest.mark.asyncio
async def test_warm_up_skips_entirely_without_admin_user_id_configured() -> None:
    settings = SimpleNamespace(gacha_admin_user_id=None)
    bot = AsyncMock()
    activity_repo = SimpleNamespace()

    await orch.warm_up_gacha_animation_cache(settings=settings, bot=bot, activity_repo=activity_repo)

    bot.send_animation.assert_not_awaited()


@pytest.mark.asyncio
async def test_warm_up_continues_past_a_single_card_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """One card failing to render/send must not abort warmup for the rest
    of the catalog."""
    orch._ready_animation_banners.clear()
    catalog = SimpleNamespace(
        cards=[
            SimpleNamespace(code="broken", name="Broken", rarity="common", rarity_label="⬜"),
            SimpleNamespace(code="amber", name="Эмбер", rarity="common", rarity_label="⬜"),
        ]
    )
    monkeypatch.setattr(orch, "_get_banner_catalog", AsyncMock(side_effect=lambda settings, banner: catalog if banner == "genshin" else SimpleNamespace(cards=[])))
    activity_repo = SimpleNamespace(
        get_gacha_animation_cached_versions_by_card=AsyncMock(return_value={}),
        get_gacha_animation_variant_count=AsyncMock(return_value=0),
        add_gacha_animation_variant=AsyncMock(),
        evict_oldest_gacha_animation_variant=AsyncMock(),
    )
    resolved = SimpleNamespace(payload=BufferedInputFile(b"gif", filename="x.gif"), needs_caching=True, cache_version="v1")
    resolve_mock = AsyncMock(side_effect=[RuntimeError("boom"), resolved])
    monkeypatch.setattr(orch, "resolve_gacha_reel_animation", resolve_mock)

    sent_message = SimpleNamespace(message_id=42, animation=SimpleNamespace(file_id="warmup-file-id"))
    bot = AsyncMock()
    bot.send_animation = AsyncMock(return_value=sent_message)
    bot.delete_message = AsyncMock()

    settings = SimpleNamespace(gacha_admin_user_id=905302972)
    await orch.warm_up_gacha_animation_cache(settings=settings, bot=bot, activity_repo=activity_repo)

    assert resolve_mock.await_count == 2  # both cards attempted despite the first failing
    activity_repo.add_gacha_animation_variant.assert_awaited_once_with(
        banner="genshin", card_code="amber", telegram_file_id="warmup-file-id", cache_version="v1"
    )


@pytest.mark.asyncio
async def test_warm_up_rolls_back_session_after_a_card_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """A failed card must roll back the shared DB session before moving on
    — otherwise, on Postgres, the aborted transaction poisons every
    subsequent operation on that session (every later commit silently
    fails) until something rolls it back, so warmup looks like it's
    running (cards get rendered/sent) but nothing after the first failure
    ever actually lands in the DB (found live, 2026-08-19: warmup ran for
    ~45 min, sent many cards, but the cached-variant count never moved past
    the point of the first failure)."""
    orch._ready_animation_banners.clear()
    catalog = SimpleNamespace(
        cards=[
            SimpleNamespace(code="broken", name="Broken", rarity="common", rarity_label="⬜"),
            SimpleNamespace(code="amber", name="Эмбер", rarity="common", rarity_label="⬜"),
        ]
    )
    monkeypatch.setattr(orch, "_get_banner_catalog", AsyncMock(side_effect=lambda settings, banner: catalog if banner == "genshin" else SimpleNamespace(cards=[])))
    activity_repo = SimpleNamespace(
        get_gacha_animation_cached_versions_by_card=AsyncMock(return_value={}),
        get_gacha_animation_variant_count=AsyncMock(return_value=0),
        add_gacha_animation_variant=AsyncMock(),
        evict_oldest_gacha_animation_variant=AsyncMock(),
    )
    resolved = SimpleNamespace(payload=BufferedInputFile(b"gif", filename="x.gif"), needs_caching=True, cache_version="v1")
    resolve_mock = AsyncMock(side_effect=[RuntimeError("boom"), resolved])
    monkeypatch.setattr(orch, "resolve_gacha_reel_animation", resolve_mock)

    sent_message = SimpleNamespace(message_id=42, animation=SimpleNamespace(file_id="warmup-file-id"))
    bot = AsyncMock()
    bot.send_animation = AsyncMock(return_value=sent_message)
    bot.delete_message = AsyncMock()
    on_card_done = AsyncMock()
    on_card_error = AsyncMock()

    settings = SimpleNamespace(gacha_admin_user_id=905302972)
    await orch.warm_up_gacha_animation_cache(
        settings=settings, bot=bot, activity_repo=activity_repo,
        on_card_done=on_card_done, on_card_error=on_card_error,
    )

    on_card_error.assert_awaited_once()  # rolled back exactly once, for the failed card
    on_card_done.assert_awaited_once()  # the successful card still committed


@pytest.mark.asyncio
async def test_warm_up_survives_on_card_error_itself_raising(monkeypatch: pytest.MonkeyPatch) -> None:
    """The rollback callback can itself raise (e.g. the underlying DB
    connection already dropped) — found live, 2026-08-19, running warmup
    against a flaky SSH-tunneled connection: one dropped connection crashed
    the whole warmup process instead of just skipping that card, wiping out
    all further progress for the rest of the catalog."""
    orch._ready_animation_banners.clear()
    catalog = SimpleNamespace(
        cards=[
            SimpleNamespace(code="broken", name="Broken", rarity="common", rarity_label="⬜"),
            SimpleNamespace(code="amber", name="Эмбер", rarity="common", rarity_label="⬜"),
        ]
    )
    monkeypatch.setattr(orch, "_get_banner_catalog", AsyncMock(side_effect=lambda settings, banner: catalog if banner == "genshin" else SimpleNamespace(cards=[])))
    activity_repo = SimpleNamespace(
        get_gacha_animation_cached_versions_by_card=AsyncMock(return_value={}),
        get_gacha_animation_variant_count=AsyncMock(return_value=0),
        add_gacha_animation_variant=AsyncMock(),
        evict_oldest_gacha_animation_variant=AsyncMock(),
    )
    resolved = SimpleNamespace(payload=BufferedInputFile(b"mp4", filename="x.mp4"), needs_caching=True, cache_version="v1")
    resolve_mock = AsyncMock(side_effect=[RuntimeError("boom"), resolved])
    monkeypatch.setattr(orch, "resolve_gacha_reel_animation", resolve_mock)

    sent_message = SimpleNamespace(message_id=42, animation=SimpleNamespace(file_id="warmup-file-id"))
    bot = AsyncMock()
    bot.send_animation = AsyncMock(return_value=sent_message)
    bot.delete_message = AsyncMock()
    on_card_done = AsyncMock()
    on_card_error = AsyncMock(side_effect=RuntimeError("connection already closed"))

    settings = SimpleNamespace(gacha_admin_user_id=905302972)
    await orch.warm_up_gacha_animation_cache(
        settings=settings, bot=bot, activity_repo=activity_repo,
        on_card_done=on_card_done, on_card_error=on_card_error,
    )  # must not raise

    on_card_error.assert_awaited_once()
    on_card_done.assert_awaited_once()  # the second card still got attempted and committed


@pytest.mark.asyncio
async def test_warm_up_skips_a_card_that_hangs_instead_of_stalling_forever(monkeypatch: pytest.MonkeyPatch) -> None:
    """A hung network call for one card (e.g. a dead connection to Telegram
    that never times out on its own) must not stall the rest of the warmup
    forever with zero log output — found live 2026-08-19: warmup stalled on
    one card for 4+ hours with no error and no further progress."""
    orch._ready_animation_banners.clear()
    monkeypatch.setattr(orch, "_WARMUP_CARD_TIMEOUT_SECONDS", 0.05)
    catalog = SimpleNamespace(
        cards=[
            SimpleNamespace(code="stuck", name="Stuck", rarity="common", rarity_label="⬜"),
            SimpleNamespace(code="amber", name="Эмбер", rarity="common", rarity_label="⬜"),
        ]
    )
    monkeypatch.setattr(orch, "_get_banner_catalog", AsyncMock(side_effect=lambda settings, banner: catalog if banner == "genshin" else SimpleNamespace(cards=[])))
    activity_repo = SimpleNamespace(
        get_gacha_animation_cached_versions_by_card=AsyncMock(return_value={}),
        get_gacha_animation_variant_count=AsyncMock(return_value=0),
        add_gacha_animation_variant=AsyncMock(),
        evict_oldest_gacha_animation_variant=AsyncMock(),
    )
    resolved = SimpleNamespace(payload=BufferedInputFile(b"gif", filename="x.gif"), needs_caching=True, cache_version="v1")

    async def resolve_side_effect(*, banner, landing_card_code, **kwargs):
        if landing_card_code == "stuck":
            await asyncio.sleep(10)  # never actually reached within the test timeout
        return resolved

    monkeypatch.setattr(orch, "resolve_gacha_reel_animation", AsyncMock(side_effect=resolve_side_effect))

    sent_message = SimpleNamespace(message_id=42, animation=SimpleNamespace(file_id="warmup-file-id"))
    bot = AsyncMock()
    bot.send_animation = AsyncMock(return_value=sent_message)
    bot.delete_message = AsyncMock()

    settings = SimpleNamespace(gacha_admin_user_id=905302972)
    await asyncio.wait_for(
        orch.warm_up_gacha_animation_cache(settings=settings, bot=bot, activity_repo=activity_repo),
        timeout=5.0,
    )

    activity_repo.add_gacha_animation_variant.assert_awaited_once_with(
        banner="genshin", card_code="amber", telegram_file_id="warmup-file-id", cache_version="v1"
    )


def test_compute_cache_version_changes_when_background_asset_changes(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    background = tmp_path / "bg.png"
    background.write_bytes(b"background-v1")
    monkeypatch.setattr(orch, "_background_path_for_banner", lambda banner: background)
    version_before = orch._compute_cache_version(banner="genshin", landing_card_etag='"etag-a"')

    background.write_bytes(b"background-v2-different-content")
    version_after = orch._compute_cache_version(banner="genshin", landing_card_etag='"etag-a"')

    assert version_before != version_after


@pytest.mark.asyncio
async def test_render_reel_gif_fetches_images_and_excludes_landing_card_from_fillers(
    monkeypatch: pytest.MonkeyPatch, tmp_path,
) -> None:
    catalog = SimpleNamespace(
        cards=[
            SimpleNamespace(code="furina", name="Фурина", rarity="mythic", rarity_label="🟥", image_url="http://x/furina.png"),
            SimpleNamespace(code="amber", name="Эмбер", rarity="common", rarity_label="⬜", image_url="http://x/amber.png"),
            SimpleNamespace(code="kafka", name="Кафка", rarity="legendary", rarity_label="🟨", image_url="http://x/kafka.png"),
        ]
    )
    monkeypatch.setattr(orch, "_get_banner_catalog", AsyncMock(return_value=catalog))
    fetch_mock = AsyncMock(return_value=b"img-bytes")
    monkeypatch.setattr(orch, "_fetch_bytes_cached", fetch_mock)
    build_mock = SimpleNamespace(called_with=None)

    def fake_build(*, background_bytes, landing_card, filler_cards):
        build_mock.called_with = (background_bytes, landing_card, filler_cards)
        return b"final-gif"

    monkeypatch.setattr(orch, "build_gacha_reel_animation", fake_build)

    result = await orch._render_reel_gif(**_render_kwargs(tmp_path))

    assert result == b"final-gif"
    _background_bytes, landing_card, filler_cards = build_mock.called_with
    assert landing_card.name == "Фурина"
    assert {card.name for card in filler_cards} == {"Эмбер", "Кафка"}
    assert all(card.name != "Фурина" for card in filler_cards)


def _render_test_catalog() -> SimpleNamespace:
    return SimpleNamespace(
        cards=[
            SimpleNamespace(code="furina", name="Фурина", rarity="mythic", rarity_label="🟥", image_url="http://x/furina.png"),
            SimpleNamespace(code="amber", name="Эмбер", rarity="common", rarity_label="⬜", image_url="http://x/amber.png"),
        ]
    )


def _render_kwargs(tmp_path) -> dict:
    return dict(
        settings=SimpleNamespace(gacha_timeout_seconds=5.0, gacha_reel_cache_dir=str(tmp_path)),
        banner="genshin",
        landing_card_code="furina",
        landing_card_name="Фурина",
        landing_rarity_label="🟥 Мифическая",
        landing_border_color=(230, 60, 70),
        cache_version="v1",
    )


@pytest.mark.asyncio
async def test_render_reel_gif_offloads_pillow_rendering_to_a_thread(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """Pre-deploy audit finding (2026-08-19): Pillow rendering is
    synchronous CPU-bound work (~4-10s) — calling it directly inside this
    coroutine would freeze the entire bot's event loop (all users, all
    chats) for the whole render, not just this one pull/warmup card. The
    render must run in a worker thread so the loop stays responsive."""
    monkeypatch.setattr(orch, "_get_banner_catalog", AsyncMock(return_value=_render_test_catalog()))
    monkeypatch.setattr(orch, "_fetch_bytes_cached", AsyncMock(return_value=b"img-bytes"))
    render_threads: list[threading.Thread] = []

    def fake_build(**kwargs):
        render_threads.append(threading.current_thread())
        return b"final-gif"

    monkeypatch.setattr(orch, "build_gacha_reel_animation", fake_build)

    result = await orch._render_reel_gif(**_render_kwargs(tmp_path))

    assert result == b"final-gif"
    assert len(render_threads) == 1
    assert render_threads[0] is not threading.main_thread()


@pytest.mark.asyncio
async def test_render_keeps_finished_mp4_on_disk_when_the_timeout_abandons_the_wait(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """A warmup card's timeout used to cancel the await before the MP4 was
    written, so a finished render was discarded and ZZZ stayed at 0/60 on
    disk. The render thread now writes the file itself."""
    monkeypatch.setattr(orch, "_get_banner_catalog", AsyncMock(return_value=_render_test_catalog()))
    monkeypatch.setattr(orch, "_fetch_bytes_cached", AsyncMock(return_value=b"img-bytes"))
    mp4 = _valid_mp4_bytes()

    def slow_build(**kwargs):
        time.sleep(0.3)
        return mp4

    monkeypatch.setattr(orch, "build_gacha_reel_animation", slow_build)
    kwargs = _render_kwargs(tmp_path)

    with pytest.raises(TimeoutError):
        await asyncio.wait_for(orch._render_reel_gif(**kwargs), timeout=0.05)
    # Wait until the abandoned render thread has finished and released the slot.
    assert await asyncio.to_thread(orch._render_slot.acquire, True, 5)
    orch._render_slot.release()

    assert orch._read_local_reel(
        settings=kwargs["settings"], banner="genshin", card_code="furina", cache_version="v1"
    ) == mp4


@pytest.mark.asyncio
async def test_pull_render_is_skipped_while_previous_render_thread_is_still_running(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """Two overlapping renders were enough to exceed the container's memory
    limit (incident 2026-10-12). While the first render's thread is alive, a
    user pull must not start a second one, and a warmup card must wait for it
    instead of rendering on top of it."""
    monkeypatch.setattr(orch, "_get_banner_catalog", AsyncMock(return_value=_render_test_catalog()))
    monkeypatch.setattr(orch, "_fetch_bytes_cached", AsyncMock(return_value=b"img-bytes"))
    release_first = threading.Event()
    active = []
    max_active = []
    lock = threading.Lock()

    def blocking_build(**kwargs):
        with lock:
            active.append(1)
            max_active.append(len(active))
        try:
            release_first.wait(timeout=5)
            return b"final-gif"
        finally:
            with lock:
                active.pop()

    monkeypatch.setattr(orch, "build_gacha_reel_animation", blocking_build)

    first = asyncio.create_task(orch._render_reel_gif(**_render_kwargs(tmp_path)))
    await asyncio.sleep(0.05)  # the first render takes the slot and starts its thread
    waiting = None
    try:
        with pytest.raises(orch.GachaRenderBusyError):
            await orch._render_reel_gif(**_render_kwargs(tmp_path))
        waiting = asyncio.create_task(orch._render_reel_gif(**_render_kwargs(tmp_path), wait_for_slot=True))
        await asyncio.sleep(0.05)
        assert not waiting.done()
    finally:
        release_first.set()

    assert await first == b"final-gif"
    assert await waiting == b"final-gif"
    assert max(max_active) == 1


@pytest.mark.asyncio
async def test_render_reel_gif_releases_render_slot_after_a_failed_render(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setattr(orch, "_get_banner_catalog", AsyncMock(return_value=_render_test_catalog()))
    monkeypatch.setattr(orch, "_fetch_bytes_cached", AsyncMock(return_value=b"img-bytes"))

    def failing_build(**kwargs):
        raise RuntimeError("pillow exploded")

    monkeypatch.setattr(orch, "build_gacha_reel_animation", failing_build)
    with pytest.raises(RuntimeError, match="pillow exploded"):
        await orch._render_reel_gif(**_render_kwargs(tmp_path))

    monkeypatch.setattr(orch, "build_gacha_reel_animation", lambda **kwargs: _valid_mp4_bytes())
    assert await orch._render_reel_gif(**_render_kwargs(tmp_path)) == _valid_mp4_bytes()


@pytest.mark.asyncio
async def test_get_banner_catalog_cold_fetch_caches_response_and_etag(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ключевое требование от пользователя: инвалидация по ETag/hash, а не
    'файл существует — не обновляем никогда'. Первый вызов всегда идёт в
    сеть без If-None-Match и запоминает вернувшийся etag."""
    orch._catalog_cache.clear()
    monkeypatch.setattr(orch.time, "monotonic", lambda: 1000.0)
    response = SimpleNamespace(banner="genshin", cards=[])
    get_mock = AsyncMock(return_value=(response, '"etag-1"'))
    monkeypatch.setattr(orch, "get_banner_cards", get_mock)

    result = await orch._get_banner_catalog(SimpleNamespace(), "genshin")

    assert result is response
    get_mock.assert_awaited_once_with(SimpleNamespace(), banner="genshin", if_none_match=None)
    assert orch._catalog_cache["genshin"].etag == '"etag-1"'


@pytest.mark.asyncio
async def test_get_banner_catalog_warm_hit_skips_network_entirely(monkeypatch: pytest.MonkeyPatch) -> None:
    orch._catalog_cache.clear()
    monkeypatch.setattr(orch.time, "monotonic", lambda: 1000.0)
    response = SimpleNamespace(banner="genshin", cards=[])
    get_mock = AsyncMock(return_value=(response, '"etag-1"'))
    monkeypatch.setattr(orch, "get_banner_cards", get_mock)
    await orch._get_banner_catalog(SimpleNamespace(), "genshin")
    get_mock.reset_mock()

    monkeypatch.setattr(orch.time, "monotonic", lambda: 1000.0 + orch._FRESH_WINDOW_SECONDS - 1)
    result = await orch._get_banner_catalog(SimpleNamespace(), "genshin")

    assert result is response
    get_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_banner_catalog_revalidates_with_etag_after_window_and_reuses_on_304(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orch._catalog_cache.clear()
    monkeypatch.setattr(orch.time, "monotonic", lambda: 1000.0)
    response = SimpleNamespace(banner="genshin", cards=[])
    get_mock = AsyncMock(return_value=(response, '"etag-1"'))
    monkeypatch.setattr(orch, "get_banner_cards", get_mock)
    await orch._get_banner_catalog(SimpleNamespace(), "genshin")

    monkeypatch.setattr(orch.time, "monotonic", lambda: 1000.0 + orch._FRESH_WINDOW_SECONDS + 1)
    get_mock.reset_mock(return_value=True)
    get_mock.return_value = (None, '"etag-1"')  # 304 Not Modified

    result = await orch._get_banner_catalog(SimpleNamespace(), "genshin")

    assert result is response  # reused, not refetched
    get_mock.assert_awaited_once_with(SimpleNamespace(), banner="genshin", if_none_match='"etag-1"')


@pytest.mark.asyncio
async def test_get_banner_catalog_replaces_cache_when_content_actually_changed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orch._catalog_cache.clear()
    monkeypatch.setattr(orch.time, "monotonic", lambda: 1000.0)
    old_response = SimpleNamespace(banner="genshin", cards=["old"])
    get_mock = AsyncMock(return_value=(old_response, '"etag-1"'))
    monkeypatch.setattr(orch, "get_banner_cards", get_mock)
    await orch._get_banner_catalog(SimpleNamespace(), "genshin")

    monkeypatch.setattr(orch.time, "monotonic", lambda: 1000.0 + orch._FRESH_WINDOW_SECONDS + 1)
    new_response = SimpleNamespace(banner="genshin", cards=["new"])
    get_mock.return_value = (new_response, '"etag-2"')

    result = await orch._get_banner_catalog(SimpleNamespace(), "genshin")

    assert result is new_response
    assert orch._catalog_cache["genshin"].etag == '"etag-2"'


@pytest.mark.asyncio
async def test_fetch_bytes_cached_never_permanently_caches_a_failed_fetch() -> None:
    """404/error responses must not poison the cache — a subsequent call
    must retry over the network, not keep returning a cached failure."""
    orch._image_cache.clear()

    class _FailingClient:
        def __init__(self) -> None:
            self.calls = 0

        async def get(self, url: str, headers: dict[str, str] | None = None):
            self.calls += 1
            request = httpx.Request("GET", url)
            return httpx.Response(404, request=request)

    client = _FailingClient()
    with pytest.raises(httpx.HTTPStatusError):
        await orch._fetch_bytes_cached(client, "http://x/missing.jpg")
    assert "http://x/missing.jpg" not in orch._image_cache

    with pytest.raises(httpx.HTTPStatusError):
        await orch._fetch_bytes_cached(client, "http://x/missing.jpg")
    assert client.calls == 2  # retried over the network both times, no poisoned cache


@pytest.mark.asyncio
async def test_fetch_bytes_cached_warm_hit_then_revalidates_after_window(monkeypatch: pytest.MonkeyPatch) -> None:
    orch._image_cache.clear()
    monkeypatch.setattr(orch.time, "monotonic", lambda: 2000.0)

    class _RecordingClient:
        def __init__(self) -> None:
            self.calls: list[dict[str, str] | None] = []

        async def get(self, url: str, headers: dict[str, str] | None = None):
            self.calls.append(headers)
            request = httpx.Request("GET", url)
            if headers and headers.get("If-None-Match") == '"img-etag"':
                return httpx.Response(304, headers={"etag": '"img-etag"'}, request=request)
            return httpx.Response(200, headers={"etag": '"img-etag"'}, content=b"portrait-bytes", request=request)

    client = _RecordingClient()
    first = await orch._fetch_bytes_cached(client, "http://x/card.jpg")
    assert first == b"portrait-bytes"
    assert len(client.calls) == 1
    assert client.calls[0] in (None, {})

    # still within the freshness window: no network call at all
    second = await orch._fetch_bytes_cached(client, "http://x/card.jpg")
    assert second == b"portrait-bytes"
    assert len(client.calls) == 1

    # window elapsed: revalidates with the remembered etag, gets 304, reuses bytes
    monkeypatch.setattr(orch.time, "monotonic", lambda: 2000.0 + orch._FRESH_WINDOW_SECONDS + 1)
    third = await orch._fetch_bytes_cached(client, "http://x/card.jpg")
    assert third == b"portrait-bytes"
    assert len(client.calls) == 2
    assert client.calls[1] == {"If-None-Match": '"img-etag"'}


FROZEN_RENDERER_SHA256 = "aa484f1ced86a093ae243b9d6a00c9189bb38e2ba9d91a19420d437dae24c8b3"


def test_frozen_renderer_copy_is_the_file_the_cached_mp4s_were_rendered_with() -> None:
    """The copy is `git show b4cb08e:src/selara/presentation/gacha_reel.py`, the renderer production ran when it stored the
    cached MP4s. Its bytes are the cache-version input, so a different copy re-keys every card."""
    assert hashlib.sha256(orch._VISUAL_RENDERER_SOURCE.read_bytes()).hexdigest() == FROZEN_RENDERER_SHA256


def test_cache_version_is_unchanged_for_the_mp4s_already_cached_in_production() -> None:
    """Pinned from the b4cb08e renderer and background image, so a deploy keeps every stored animation valid. A change
    here means the whole catalog is re-rendered."""
    assert orch._compute_cache_version(banner="genshin", landing_card_etag='"etag-a"') == "aa8ba92b270c937b"


@pytest.mark.asyncio
async def test_resolve_without_render_returns_none_when_the_card_has_nothing_cached(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    activity_repo = SimpleNamespace(
        get_gacha_animation_variant_count=AsyncMock(return_value=0),
        get_random_gacha_animation_variant_file_id=AsyncMock(),
    )
    render_mock = AsyncMock()
    monkeypatch.setattr(orch, "_render_reel_gif", render_mock)

    result = await orch.resolve_gacha_reel_animation(
        settings=SimpleNamespace(gacha_reel_cache_dir=str(tmp_path)),
        activity_repo=activity_repo,
        banner="genshin",
        landing_card_code="furina",
        landing_card_name="Фурина",
        landing_rarity="mythic",
        landing_rarity_label="🟥 Мифическая",
        allow_render=False,
    )

    assert result is None
    render_mock.assert_not_awaited()
    activity_repo.get_random_gacha_animation_variant_file_id.assert_not_awaited()


@pytest.mark.asyncio
async def test_resolve_without_render_falls_back_to_cached_file_id_when_no_disk_copy_exists(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    activity_repo = SimpleNamespace(
        get_gacha_animation_variant_count=AsyncMock(return_value=1),
        get_random_gacha_animation_variant_file_id=AsyncMock(return_value="cached-file-id"),
    )
    render_mock = AsyncMock()
    monkeypatch.setattr(orch, "_render_reel_gif", render_mock)

    result = await orch.resolve_gacha_reel_animation(
        settings=SimpleNamespace(gacha_reel_cache_dir=str(tmp_path)),
        activity_repo=activity_repo,
        banner="genshin",
        landing_card_code="furina",
        landing_card_name="Фурина",
        landing_rarity="mythic",
        landing_rarity_label="🟥 Мифическая",
        allow_render=False,
    )

    assert result.payload == "cached-file-id"
    assert result.needs_caching is False
    render_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_ensure_gacha_reel_on_disk_renders_a_missing_copy_without_waiting_for_the_slot(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    render_mock = AsyncMock(return_value=_valid_mp4_bytes())
    monkeypatch.setattr(orch, "_render_reel_gif", render_mock)

    await orch.ensure_gacha_reel_on_disk(
        settings=SimpleNamespace(gacha_reel_cache_dir=str(tmp_path)),
        banner="genshin",
        landing_card_code="furina",
        landing_card_name="Фурина",
        landing_rarity="mythic",
        landing_rarity_label="🟥 Мифическая",
    )

    render_mock.assert_awaited_once()
    assert render_mock.await_args.kwargs["wait_for_slot"] is False
    assert render_mock.await_args.kwargs["cache_version"] == "v1"
    assert render_mock.await_args.kwargs["landing_border_color"] == (230, 60, 70)
    assert not orch._reels_being_rendered


@pytest.mark.asyncio
async def test_ensure_gacha_reel_on_disk_skips_a_card_whose_copy_is_already_on_disk(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    settings = SimpleNamespace(gacha_reel_cache_dir=str(tmp_path))
    orch._write_local_reel_atomic(
        settings=settings, banner="genshin", card_code="furina", cache_version="v1", content=_valid_mp4_bytes()
    )
    render_mock = AsyncMock()
    monkeypatch.setattr(orch, "_render_reel_gif", render_mock)

    await orch.ensure_gacha_reel_on_disk(
        settings=settings,
        banner="genshin",
        landing_card_code="furina",
        landing_card_name="Фурина",
        landing_rarity="mythic",
        landing_rarity_label="🟥 Мифическая",
    )

    render_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_ensure_gacha_reel_on_disk_leaves_the_card_for_later_when_the_slot_is_busy_and_never_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    settings = SimpleNamespace(gacha_reel_cache_dir=str(tmp_path))
    kwargs = dict(
        settings=settings,
        banner="genshin",
        landing_card_code="furina",
        landing_card_name="Фурина",
        landing_rarity="mythic",
        landing_rarity_label="🟥 Мифическая",
    )
    monkeypatch.setattr(orch, "_render_reel_gif", AsyncMock(side_effect=orch.GachaRenderBusyError("busy")))
    await orch.ensure_gacha_reel_on_disk(**kwargs)
    assert not orch._reels_being_rendered

    monkeypatch.setattr(orch, "_render_reel_gif", AsyncMock(side_effect=RuntimeError("pillow exploded")))
    await orch.ensure_gacha_reel_on_disk(**kwargs)
    assert not orch._reels_being_rendered


@pytest.mark.asyncio
async def test_slot_wait_gives_up_after_its_budget_while_a_render_holds_the_slot() -> None:
    assert orch._render_slot.acquire(blocking=False)
    try:
        with pytest.raises(orch.GachaRenderBusyError):
            await orch._acquire_render_slot(wait=True, timeout=0.05)
    finally:
        orch._render_slot.release()


@pytest.mark.asyncio
async def test_render_budget_cuts_the_wait_but_the_render_still_lands_on_disk(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setattr(orch, "_get_banner_catalog", AsyncMock(return_value=_render_test_catalog()))
    monkeypatch.setattr(orch, "_fetch_bytes_cached", AsyncMock(return_value=b"img-bytes"))
    mp4 = _valid_mp4_bytes()

    def slow_build(**kwargs):
        time.sleep(0.3)
        return mp4

    monkeypatch.setattr(orch, "build_gacha_reel_animation", slow_build)
    kwargs = _render_kwargs(tmp_path)

    with pytest.raises(TimeoutError):
        await orch._render_reel_gif(**kwargs, render_timeout=0.05)
    assert await asyncio.to_thread(orch._render_slot.acquire, True, 5)
    orch._render_slot.release()

    assert orch._read_local_reel(
        settings=kwargs["settings"], banner="genshin", card_code="furina", cache_version="v1"
    ) == mp4


@pytest.mark.asyncio
async def test_a_render_that_waited_for_the_slot_reuses_the_copy_an_abandoned_render_wrote(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """A render cut off by its budget keeps running and still writes its MP4. A retry that was waiting for the slot must
    take that copy once the slot is free, not render the same card a second time."""
    monkeypatch.setattr(orch, "_get_banner_catalog", AsyncMock(return_value=_render_test_catalog()))
    monkeypatch.setattr(orch, "_fetch_bytes_cached", AsyncMock(return_value=b"img-bytes"))
    rendered: list[bytes] = []

    def must_not_render(**_kwargs) -> bytes:
        rendered.append(b"second render")
        return b"second render"

    monkeypatch.setattr(orch, "build_gacha_reel_animation", must_not_render)
    kwargs = _render_kwargs(tmp_path)
    mp4 = _valid_mp4_bytes()
    assert orch._render_slot.acquire(blocking=False)  # the abandoned render still holds the slot

    async def finish_the_abandoned_render() -> None:
        await asyncio.sleep(0.05)
        orch._write_local_reel_atomic(
            settings=kwargs["settings"], banner="genshin", card_code="furina", cache_version="v1", content=mp4
        )
        orch._render_slot.release()

    finisher = asyncio.create_task(finish_the_abandoned_render())
    try:
        result = await orch._render_reel_gif(**kwargs, wait_for_slot=True, slot_timeout=5)
    finally:
        await finisher

    assert result == mp4
    assert rendered == []


def _warm_up_catalog(*card_codes: str) -> SimpleNamespace:
    return SimpleNamespace(
        cards=[SimpleNamespace(code=code, name=code.title(), rarity="common", rarity_label="⬜") for code in card_codes]
    )


def _warm_up_repo() -> SimpleNamespace:
    return SimpleNamespace(
        get_gacha_animation_cached_versions_by_card=AsyncMock(return_value={}),
        get_gacha_animation_variant_count=AsyncMock(return_value=0),
        add_gacha_animation_variant=AsyncMock(),
        evict_oldest_gacha_animation_variant=AsyncMock(),
    )


@pytest.mark.asyncio
async def test_warm_up_retries_a_card_that_found_the_render_slot_busy_after_the_rest_of_the_catalog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog = _warm_up_catalog("furina", "amber")
    monkeypatch.setattr(
        orch, "_get_banner_catalog", AsyncMock(side_effect=lambda settings, banner: catalog if banner == "genshin" else SimpleNamespace(cards=[]))
    )
    activity_repo = _warm_up_repo()
    resolved = SimpleNamespace(payload=BufferedInputFile(b"mp4", filename="x.mp4"), needs_caching=True, cache_version="v1")
    calls: list[str] = []

    async def resolve_side_effect(*, landing_card_code, **kwargs):
        calls.append(landing_card_code)
        if landing_card_code == "furina" and calls.count("furina") == 1:
            raise orch.GachaRenderBusyError("busy")
        return resolved

    resolve_mock = AsyncMock(side_effect=resolve_side_effect)
    monkeypatch.setattr(orch, "resolve_gacha_reel_animation", resolve_mock)
    bot = AsyncMock()
    bot.send_animation = AsyncMock(return_value=SimpleNamespace(message_id=42, animation=SimpleNamespace(file_id="warmup-file-id")))
    bot.delete_message = AsyncMock()

    await orch.warm_up_gacha_animation_cache(settings=SimpleNamespace(gacha_admin_user_id=905302972), bot=bot, activity_repo=activity_repo)

    # furina was deferred while the slot was busy, amber went first, and furina gets its one retry at the end
    assert calls == ["furina", "amber", "furina"]
    assert activity_repo.add_gacha_animation_variant.await_count == 2
    assert resolve_mock.await_args.kwargs["render_slot_timeout"] == orch._WARMUP_SLOT_WAIT_SECONDS
    assert resolve_mock.await_args.kwargs["render_timeout"] == orch._WARMUP_RENDER_SECONDS


@pytest.mark.asyncio
async def test_warm_up_defers_a_card_whose_upload_runs_out_of_time_and_sends_it_again_later(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(orch, "_WARMUP_UPLOAD_SECONDS", 0.05)
    monkeypatch.setattr(orch, "_get_banner_catalog", AsyncMock(side_effect=lambda settings, banner: _warm_up_catalog("amber") if banner == "genshin" else SimpleNamespace(cards=[])))
    activity_repo = _warm_up_repo()
    resolved = SimpleNamespace(payload=BufferedInputFile(b"mp4", filename="x.mp4"), needs_caching=True, cache_version="v1")
    monkeypatch.setattr(orch, "resolve_gacha_reel_animation", AsyncMock(return_value=resolved))
    attempts: list[dict] = []
    sent_message = SimpleNamespace(message_id=42, animation=SimpleNamespace(file_id="warmup-file-id"))

    async def send_animation(**kwargs):
        attempts.append(kwargs)
        if len(attempts) == 1:
            await asyncio.sleep(10)  # cut off by the upload budget
        return sent_message

    bot = AsyncMock()
    bot.send_animation = AsyncMock(side_effect=send_animation)
    bot.delete_message = AsyncMock()

    await orch.warm_up_gacha_animation_cache(settings=SimpleNamespace(gacha_admin_user_id=905302972), bot=bot, activity_repo=activity_repo)

    assert len(attempts) == 2
    activity_repo.add_gacha_animation_variant.assert_awaited_once_with(
        banner="genshin", card_code="amber", telegram_file_id="warmup-file-id", cache_version="v1"
    )


@pytest.mark.asyncio
async def test_a_render_that_cannot_get_the_slot_downloads_no_portraits(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """Portraits used to be fetched before the slot was taken, so every pull on a cold card downloaded eleven of them
    even though only one render could run. A task that is turned away now fetches nothing."""
    monkeypatch.setattr(orch, "_get_banner_catalog", AsyncMock(return_value=_render_test_catalog()))
    fetch_mock = AsyncMock(return_value=b"img-bytes")
    monkeypatch.setattr(orch, "_fetch_bytes_cached", fetch_mock)
    assert orch._render_slot.acquire(blocking=False)  # another render holds the slot
    try:
        with pytest.raises(orch.GachaRenderBusyError):
            await orch._render_reel_gif(**_render_kwargs(tmp_path))
    finally:
        orch._render_slot.release()

    fetch_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_waiting_render_fetches_portraits_only_once_it_holds_the_slot(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setattr(orch, "_get_banner_catalog", AsyncMock(return_value=_render_test_catalog()))
    fetch_mock = AsyncMock(return_value=b"img-bytes")
    monkeypatch.setattr(orch, "_fetch_bytes_cached", fetch_mock)
    monkeypatch.setattr(orch, "build_gacha_reel_animation", lambda **kwargs: _valid_mp4_bytes())
    assert orch._render_slot.acquire(blocking=False)
    fetched_while_waiting: list[bool] = []

    async def release_slot_after_a_while() -> None:
        await asyncio.sleep(0.05)
        fetched_while_waiting.append(fetch_mock.await_count > 0)
        orch._render_slot.release()

    releaser = asyncio.create_task(release_slot_after_a_while())
    try:
        result = await orch._render_reel_gif(**_render_kwargs(tmp_path), wait_for_slot=True, slot_timeout=5)
    finally:
        await releaser

    assert fetched_while_waiting == [False]
    assert fetch_mock.await_count == 2  # one filler and the landing card, fetched once by the task that rendered
    assert result == _valid_mp4_bytes()


@pytest.mark.asyncio
async def test_render_releases_the_slot_when_a_portrait_download_fails(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setattr(orch, "_get_banner_catalog", AsyncMock(return_value=_render_test_catalog()))
    monkeypatch.setattr(orch, "_fetch_bytes_cached", AsyncMock(side_effect=httpx.ConnectError("network down")))

    with pytest.raises(httpx.ConnectError):
        await orch._render_reel_gif(**_render_kwargs(tmp_path))

    assert orch._render_slot.acquire(blocking=False)
    orch._render_slot.release()


@pytest.mark.asyncio
async def test_render_releases_the_slot_when_its_downloads_are_cut_off(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """A download that is still running when a caller gives up is cancelled while the slot is held. The slot must still
    come back, because no render thread exists yet to release it."""

    async def hanging_fetch(client, url) -> bytes:
        await asyncio.sleep(10)
        return b"img-bytes"

    monkeypatch.setattr(orch, "_get_banner_catalog", AsyncMock(return_value=_render_test_catalog()))
    monkeypatch.setattr(orch, "_fetch_bytes_cached", hanging_fetch)

    with pytest.raises(TimeoutError):
        await asyncio.wait_for(orch._render_reel_gif(**_render_kwargs(tmp_path)), timeout=0.05)

    assert orch._render_slot.acquire(blocking=False)
    orch._render_slot.release()


@pytest.mark.asyncio
async def test_a_failed_portrait_download_cancels_its_siblings_before_the_slot_is_given_back(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """The slot is given back as soon as the render call fails. A sibling download still running then would keep a request
    open against a client that is already closing, while another render starts downloading beside it."""
    monkeypatch.setattr(orch, "_get_banner_catalog", AsyncMock(return_value=_render_test_catalog()))
    calls: list[str] = []
    sibling_cancelled: list[bool] = []

    async def fetch(client, url) -> bytes:
        calls.append(url)
        if len(calls) == 1:
            raise httpx.ConnectError("network down")
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            sibling_cancelled.append(True)
            raise
        return b"img-bytes"

    monkeypatch.setattr(orch, "_fetch_bytes_cached", fetch)

    with pytest.raises(httpx.ConnectError):
        await orch._render_reel_gif(**_render_kwargs(tmp_path))

    assert len(calls) > 1
    assert len(sibling_cancelled) == len(calls) - 1  # every sibling had finished cancelling when the error surfaced
    assert orch._render_slot.acquire(blocking=False)
    orch._render_slot.release()


@pytest.mark.asyncio
async def test_a_hung_admin_delete_keeps_the_cached_file_id_and_the_card_is_not_sent_again(monkeypatch: pytest.MonkeyPatch) -> None:
    """The delete used to share the upload budget and the transaction. Once Telegram had accepted the file, a hung delete
    timed the card out, the session rolled back the file_id, and the next pass uploaded the same MP4 again."""
    orch._ready_animation_banners.clear()
    monkeypatch.setattr(orch, "_WARMUP_DELETE_SECONDS", 0.05)
    monkeypatch.setattr(
        orch, "_get_banner_catalog",
        AsyncMock(side_effect=lambda settings, banner: _warm_up_catalog("amber") if banner == "genshin" else SimpleNamespace(cards=[])),
    )
    activity_repo = _warm_up_repo()
    resolved = SimpleNamespace(payload=BufferedInputFile(b"mp4", filename="x.mp4"), needs_caching=True, cache_version="v1")
    monkeypatch.setattr(orch, "resolve_gacha_reel_animation", AsyncMock(return_value=resolved))
    events: list[str] = []

    async def hung_delete(**_kwargs) -> None:
        events.append("delete")
        await asyncio.sleep(10)

    bot = AsyncMock()
    bot.send_animation = AsyncMock(return_value=SimpleNamespace(message_id=42, animation=SimpleNamespace(file_id="warmup-file-id")))
    bot.delete_message = AsyncMock(side_effect=hung_delete)
    on_card_done = AsyncMock(side_effect=lambda: events.append("commit"))
    on_card_error = AsyncMock()

    await asyncio.wait_for(
        orch.warm_up_gacha_animation_cache(
            settings=SimpleNamespace(gacha_admin_user_id=905302972), bot=bot, activity_repo=activity_repo,
            on_card_done=on_card_done, on_card_error=on_card_error,
        ),
        timeout=5,
    )

    bot.send_animation.assert_awaited_once()
    activity_repo.add_gacha_animation_variant.assert_awaited_once_with(
        banner="genshin", card_code="amber", telegram_file_id="warmup-file-id", cache_version="v1"
    )
    on_card_error.assert_not_awaited()
    assert events == ["commit", "delete"]  # committed first, then the delete was attempted


@pytest.mark.asyncio
async def test_a_failed_admin_delete_does_not_roll_back_the_card(monkeypatch: pytest.MonkeyPatch) -> None:
    orch._ready_animation_banners.clear()
    monkeypatch.setattr(
        orch, "_get_banner_catalog",
        AsyncMock(side_effect=lambda settings, banner: _warm_up_catalog("amber") if banner == "genshin" else SimpleNamespace(cards=[])),
    )
    activity_repo = _warm_up_repo()
    resolved = SimpleNamespace(payload=BufferedInputFile(b"mp4", filename="x.mp4"), needs_caching=True, cache_version="v1")
    monkeypatch.setattr(orch, "resolve_gacha_reel_animation", AsyncMock(return_value=resolved))
    bot = AsyncMock()
    bot.send_animation = AsyncMock(return_value=SimpleNamespace(message_id=42, animation=SimpleNamespace(file_id="warmup-file-id")))
    bot.delete_message = AsyncMock(side_effect=RuntimeError("telegram said no"))
    on_card_done = AsyncMock()
    on_card_error = AsyncMock()

    await orch.warm_up_gacha_animation_cache(
        settings=SimpleNamespace(gacha_admin_user_id=905302972), bot=bot, activity_repo=activity_repo,
        on_card_done=on_card_done, on_card_error=on_card_error,
    )

    on_card_done.assert_awaited_once()
    on_card_error.assert_not_awaited()
    activity_repo.add_gacha_animation_variant.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_slow_cache_write_after_an_accepted_upload_is_not_cut_off_by_the_upload_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only the send is bounded by the upload budget. Once Telegram has accepted the file, a slow cache write must finish,
    or the card is sent again on the next pass."""
    orch._ready_animation_banners.clear()
    monkeypatch.setattr(orch, "_WARMUP_UPLOAD_SECONDS", 0.05)
    monkeypatch.setattr(
        orch, "_get_banner_catalog",
        AsyncMock(side_effect=lambda settings, banner: _warm_up_catalog("amber") if banner == "genshin" else SimpleNamespace(cards=[])),
    )
    activity_repo = _warm_up_repo()

    async def slow_add(**_kwargs) -> None:
        await asyncio.sleep(0.2)

    activity_repo.add_gacha_animation_variant = AsyncMock(side_effect=slow_add)
    resolved = SimpleNamespace(payload=BufferedInputFile(b"mp4", filename="x.mp4"), needs_caching=True, cache_version="v1")
    monkeypatch.setattr(orch, "resolve_gacha_reel_animation", AsyncMock(return_value=resolved))
    bot = AsyncMock()
    bot.send_animation = AsyncMock(return_value=SimpleNamespace(message_id=42, animation=SimpleNamespace(file_id="warmup-file-id")))
    bot.delete_message = AsyncMock()

    await orch.warm_up_gacha_animation_cache(settings=SimpleNamespace(gacha_admin_user_id=905302972), bot=bot, activity_repo=activity_repo)

    bot.send_animation.assert_awaited_once()
    activity_repo.add_gacha_animation_variant.assert_awaited_once()
