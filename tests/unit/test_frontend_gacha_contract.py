from pathlib import Path


def test_gacha_page_does_not_invent_pity_or_fake_price() -> None:
    page = (Path(__file__).parents[2] / "frontend" / "src" / "pages" / "gacha" / "page.tsx").read_text(
        encoding="utf-8"
    )

    assert "localStorage" not in page
    assert "До гаранта" not in page
    assert "Soft-pity" not in page
    assert "1 600 pts" not in page
