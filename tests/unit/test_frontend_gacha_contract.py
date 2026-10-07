from pathlib import Path

FRONTEND = Path(__file__).parents[2] / "frontend" / "src"


def _read(*parts: str) -> str:
    return (FRONTEND.joinpath(*parts)).read_text(encoding="utf-8")


def test_gacha_page_does_not_invent_pity_or_fake_price() -> None:
    page = _read("pages", "gacha", "page.tsx")

    assert "localStorage" not in page
    assert "До гаранта" not in page
    assert "Soft-pity" not in page
    assert "1 600 pts" not in page


def test_gacha_page_reads_go_through_the_authenticated_app_api() -> None:
    """The Mini App gacha page must not call the token-gated gacha endpoints directly.

    The browser has no ``X-Gacha-Service-Token`` and the public nginx proxy deliberately injects
    none, so a direct ``/v1/gacha/users/{id}/...`` read is a 403. The session-authenticated app
    proxy is the only correct path.
    """
    page = _read("pages", "gacha", "page.tsx")
    client = _read("shared", "api", "gachaClient.ts")

    assert "/v1/gacha/users/" not in page
    assert "/v1/gacha/users/" not in client
    assert "viewer.telegram_user_id" not in page

    assert "/miniapp/gacha" in client
    assert "'/profile'" in client or '"/profile"' in client
    assert "'/collection'" in client or '"/collection"' in client


def test_gacha_card_images_still_use_the_public_gacha_proxy() -> None:
    """Only the data endpoints move behind the app API; ``/images/...`` stays publicly proxied."""
    client = _read("shared", "api", "gachaClient.ts")

    assert "normalizeGachaImageUrl" in client
    assert "resolveAppPath('/gacha')" in client
