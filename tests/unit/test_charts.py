from io import BytesIO

from PIL import Image

from selara.domain.entities import LeaderboardItem
from selara.presentation.charts import build_leaderboard_chart
from selara.presentation.font_support import matplotlib_text_families


def test_matplotlib_text_families_include_emoji_fallbacks() -> None:
    families = matplotlib_text_families()

    assert families
    assert "Noto Color Emoji" in families
    assert "Symbola" in families


def test_build_leaderboard_chart_accepts_emoji_names() -> None:
    chart = build_leaderboard_chart(
        [
            LeaderboardItem(
                user_id=1,
                username=None,
                first_name="😀 Бейкус",
                last_name=None,
                activity_value=42,
                karma_value=7,
                hybrid_score=12.5,
                last_seen_at=None,
                chat_display_name="😀 Бейкус",
            ),
            LeaderboardItem(
                user_id=2,
                username=None,
                first_name="😎 Лиза",
                last_name=None,
                activity_value=37,
                karma_value=5,
                hybrid_score=10.2,
                last_seen_at=None,
                chat_display_name="😎 Лиза",
            ),
        ],
        mode="activity",
    )

    assert chart is not None

    image = Image.open(BytesIO(chart))
    assert image.format == "PNG"
