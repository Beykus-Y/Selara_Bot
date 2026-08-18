from __future__ import annotations

import asyncio
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from aiogram.types import BufferedInputFile
from PIL import Image

from selara.presentation import gacha_reel_orchestration as orch


def _valid_gif_bytes() -> bytes:
    """A real minimal animated GIF — _read_local_gif validates structure
    (pre-deploy audit finding, 2026-08-19), so plain placeholder bytes like
    b"gif-bytes" no longer round-trip through the local-disk tier."""
    buffer = BytesIO()
    frame1 = Image.new("P", (4, 4))
    frame2 = Image.new("P", (4, 4))
    frame1.save(buffer, format="GIF", save_all=True, append_images=[frame2], duration=100, loop=0)
    return buffer.getvalue()


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
    gif_bytes = _valid_gif_bytes()
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
    assert result.needs_caching is True
    assert result.cache_version == "v1"
    render_mock.assert_awaited_once()
    activity_repo.get_random_gacha_animation_variant_file_id.assert_not_awaited()
    # rendered bytes must also have been written to the local disk tier
    assert orch._read_local_gif(
        settings=SimpleNamespace(gacha_reel_cache_dir=str(tmp_path)), banner="genshin", card_code="furina", cache_version="v1"
    ) == gif_bytes


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
    gif_bytes = _valid_gif_bytes()
    orch._write_local_gif_atomic(settings=settings, banner="genshin", card_code="furina", cache_version="v1", content=gif_bytes)
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


def test_write_local_gif_atomic_round_trip_and_no_partial_file_on_crash(tmp_path) -> None:
    settings = SimpleNamespace(gacha_reel_cache_dir=str(tmp_path))
    assert orch._read_local_gif(settings=settings, banner="genshin", card_code="furina", cache_version="v1") is None

    gif_bytes = _valid_gif_bytes()
    orch._write_local_gif_atomic(settings=settings, banner="genshin", card_code="furina", cache_version="v1", content=gif_bytes)

    assert orch._read_local_gif(settings=settings, banner="genshin", card_code="furina", cache_version="v1") == gif_bytes
    # no stray .tmp file left behind after a successful atomic write
    card_dir = orch._local_gif_path(settings=settings, banner="genshin", card_code="furina", cache_version="v1").parent
    assert list(card_dir.glob("*.tmp")) == []


def test_write_local_gif_atomic_replaces_stale_version_and_cleans_up_old_file(tmp_path) -> None:
    settings = SimpleNamespace(gacha_reel_cache_dir=str(tmp_path))
    old_bytes = _valid_gif_bytes()
    new_bytes = _valid_gif_bytes()
    orch._write_local_gif_atomic(settings=settings, banner="genshin", card_code="furina", cache_version="v1", content=old_bytes)
    orch._write_local_gif_atomic(settings=settings, banner="genshin", card_code="furina", cache_version="v2", content=new_bytes)

    assert orch._read_local_gif(settings=settings, banner="genshin", card_code="furina", cache_version="v1") is None
    assert orch._read_local_gif(settings=settings, banner="genshin", card_code="furina", cache_version="v2") == new_bytes


def test_read_local_gif_missing_file_returns_none_not_an_error(tmp_path) -> None:
    settings = SimpleNamespace(gacha_reel_cache_dir=str(tmp_path))
    assert orch._read_local_gif(settings=settings, banner="genshin", card_code="unknown", cache_version="v1") is None


def test_read_local_gif_rejects_corrupted_or_truncated_file(tmp_path) -> None:
    """Pre-deploy audit finding (2026-08-19): atomic writes prevent *new*
    corruption, but don't protect against a file that's already
    truncated/corrupt on disk for any other reason (partial disk write,
    manual tampering, a pre-fix crash). Reading raw bytes without
    validating the GIF structure would hand Telegram a broken file
    silently. Must fall back to Tier 3 (render) instead of trusting it."""
    settings = SimpleNamespace(gacha_reel_cache_dir=str(tmp_path))
    path = orch._local_gif_path(settings=settings, banner="genshin", card_code="furina", cache_version="v1")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"not a real gif, just garbage bytes")

    assert orch._read_local_gif(settings=settings, banner="genshin", card_code="furina", cache_version="v1") is None


def test_read_local_gif_accepts_a_genuine_animated_gif(tmp_path) -> None:
    from io import BytesIO

    from PIL import Image

    frame1 = Image.new("P", (10, 10))
    frame2 = Image.new("P", (10, 10))
    buffer = BytesIO()
    frame1.save(buffer, format="GIF", save_all=True, append_images=[frame2], duration=100, loop=0)
    valid_gif_bytes = buffer.getvalue()

    settings = SimpleNamespace(gacha_reel_cache_dir=str(tmp_path))
    orch._write_local_gif_atomic(settings=settings, banner="genshin", card_code="furina", cache_version="v1", content=valid_gif_bytes)

    result = orch._read_local_gif(settings=settings, banner="genshin", card_code="furina", cache_version="v1")
    assert result == valid_gif_bytes


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


@pytest.mark.asyncio
async def test_cache_ready_false_when_a_card_has_no_variant_at_all(monkeypatch: pytest.MonkeyPatch) -> None:
    orch._animation_cache_ready = False
    catalogs = {
        "genshin": SimpleNamespace(cards=[SimpleNamespace(code="furina"), SimpleNamespace(code="amber")]),
        "hsr": SimpleNamespace(cards=[SimpleNamespace(code="kafka")]),
    }
    monkeypatch.setattr(orch, "_get_banner_catalog", AsyncMock(side_effect=lambda settings, banner: catalogs[banner]))
    activity_repo = SimpleNamespace(
        get_gacha_animation_cached_versions_by_card=AsyncMock(
            side_effect=lambda *, banner: {"furina": {"v1"}} if banner == "genshin" else {"kafka": {"v1"}}
        )
    )

    result = await orch.is_gacha_animation_cache_ready(SimpleNamespace(), activity_repo)

    assert result is False  # "amber" has no variant at all
    assert orch._animation_cache_ready is False


@pytest.mark.asyncio
async def test_cache_ready_true_and_sticky_once_every_card_has_a_variant(monkeypatch: pytest.MonkeyPatch) -> None:
    orch._animation_cache_ready = False
    catalogs = {
        "genshin": SimpleNamespace(cards=[SimpleNamespace(code="furina")]),
        "hsr": SimpleNamespace(cards=[SimpleNamespace(code="kafka")]),
    }
    get_catalog_mock = AsyncMock(side_effect=lambda settings, banner: catalogs[banner])
    monkeypatch.setattr(orch, "_get_banner_catalog", get_catalog_mock)
    activity_repo = SimpleNamespace(
        get_gacha_animation_cached_versions_by_card=AsyncMock(
            side_effect=lambda *, banner: {"furina": {"v1"}} if banner == "genshin" else {"kafka": {"v1"}}
        )
    )

    first = await orch.is_gacha_animation_cache_ready(SimpleNamespace(), activity_repo)
    assert first is True
    assert get_catalog_mock.await_count == 2

    # sticky: a second call must not re-check the DB/catalog at all
    second = await orch.is_gacha_animation_cache_ready(SimpleNamespace(), activity_repo)
    assert second is True
    assert get_catalog_mock.await_count == 2


@pytest.mark.asyncio
async def test_warm_up_generates_only_missing_cards_and_deletes_warmup_message(monkeypatch: pytest.MonkeyPatch) -> None:
    orch._animation_cache_ready = False
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
async def test_warm_up_calls_on_card_done_after_each_successful_card(monkeypatch: pytest.MonkeyPatch) -> None:
    """A restart must not throw away already-processed cards: warmup should
    commit incrementally (per card), not once at the very end — otherwise a
    restart mid-warmup re-does DB work for cards whose (cheap) local-disk
    GIF already exists (see docs/GACHA_MODERNIZATION_TODO.md, раздел 12)."""
    orch._animation_cache_ready = False
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
    orch._animation_cache_ready = False
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
async def test_warm_up_skips_a_card_that_hangs_instead_of_stalling_forever(monkeypatch: pytest.MonkeyPatch) -> None:
    """A hung network call for one card (e.g. a dead connection to Telegram
    that never times out on its own) must not stall the rest of the warmup
    forever with zero log output — found live 2026-08-19: warmup stalled on
    one card for 4+ hours with no error and no further progress."""
    orch._animation_cache_ready = False
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
    monkeypatch: pytest.MonkeyPatch,
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

    result = await orch._render_reel_gif(
        settings=SimpleNamespace(gacha_timeout_seconds=5.0),
        banner="genshin",
        landing_card_code="furina",
        landing_card_name="Фурина",
        landing_rarity_label="🟥 Мифическая",
        landing_border_color=(230, 60, 70),
    )

    assert result == b"final-gif"
    _background_bytes, landing_card, filler_cards = build_mock.called_with
    assert landing_card.name == "Фурина"
    assert {card.name for card in filler_cards} == {"Эмбер", "Кафка"}
    assert all(card.name != "Фурина" for card in filler_cards)


@pytest.mark.asyncio
async def test_render_reel_gif_offloads_pillow_rendering_to_a_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pre-deploy audit finding (2026-08-19): Pillow rendering is
    synchronous CPU-bound work (~4-10s) — calling it directly inside this
    coroutine would freeze the entire bot's event loop (all users, all
    chats) for the whole render, not just this one pull/warmup card. Must
    run via asyncio.to_thread so the loop stays responsive."""
    catalog = SimpleNamespace(
        cards=[
            SimpleNamespace(code="furina", name="Фурина", rarity="mythic", rarity_label="🟥", image_url="http://x/furina.png"),
            SimpleNamespace(code="amber", name="Эмбер", rarity="common", rarity_label="⬜", image_url="http://x/amber.png"),
        ]
    )
    monkeypatch.setattr(orch, "_get_banner_catalog", AsyncMock(return_value=catalog))
    monkeypatch.setattr(orch, "_fetch_bytes_cached", AsyncMock(return_value=b"img-bytes"))
    monkeypatch.setattr(orch, "build_gacha_reel_animation", lambda **kwargs: b"final-gif")

    to_thread_calls = []
    real_to_thread = asyncio.to_thread

    async def spy_to_thread(func, *args, **kwargs):
        to_thread_calls.append((func, args, kwargs))
        return await real_to_thread(func, *args, **kwargs)

    monkeypatch.setattr(orch.asyncio, "to_thread", spy_to_thread)

    result = await orch._render_reel_gif(
        settings=SimpleNamespace(gacha_timeout_seconds=5.0),
        banner="genshin",
        landing_card_code="furina",
        landing_card_name="Фурина",
        landing_rarity_label="🟥 Мифическая",
        landing_border_color=(230, 60, 70),
    )

    assert result == b"final-gif"
    assert len(to_thread_calls) == 1
    assert to_thread_calls[0][0] is orch.build_gacha_reel_animation


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
