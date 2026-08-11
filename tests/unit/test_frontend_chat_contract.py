from pathlib import Path


def test_find_me_pagination_uses_server_page_and_new_click_repeats_request() -> None:
    page = (Path(__file__).parents[2] / "frontend" / "src" / "pages" / "chat" / "page.tsx").read_text(
        encoding="utf-8"
    )

    assert "setFindMeRequest((current) => current + 1)" in page
    assert "setPage(Math.max(1, leaderboardQuery.data.page - 1))" in page
    assert "setPage(leaderboardQuery.data.page + 1)" in page
