from io import BytesIO

import pytest
from PIL import Image

from selara.presentation.renderer_service import PlaywrightRendererService
from selara.presentation.family_tree import (
    _font_candidates,
    _load_font,
    _split_text_runs,
    build_family_tree_image,
)


@pytest.fixture(autouse=True)
async def use_renderer():
    renderer = PlaywrightRendererService.get_instance()
    await renderer.start()
    yield
    await renderer.stop()


async def test_build_family_tree_image_uses_dashboard_palette() -> None:
    image_bytes = await build_family_tree_image(
        subject_label="@BeykusY",
        parents=["@ParentOne", "@ParentTwo"],
        step_parents=["@StepParent"],
        spouse="@Partner",
        siblings=["@Sibling"],
        children=["@Child"],
        pets=["@Pet"],
        grandparents=["@GrandOne"],
    )

    image = Image.open(BytesIO(image_bytes))
    colors = image.getcolors(maxcolors=image.width * image.height)
    assert colors is not None
    palette = {color[:3] for _, color in colors}

    assert image.format == "PNG"
    assert image.getpixel((0, 0))[:3] == (10, 14, 26)
    # Check that new palette colors are in the palette
    assert (111, 168, 255) in palette  # Parents
    assert (245, 181, 68) in palette  # Spouse
    assert (139, 124, 246) in palette  # Child
    assert (255, 107, 138) in palette  # Pet


def test_family_tree_uses_freetype_font_for_cyrillic() -> None:
    if not _font_candidates(bold=True):
        pytest.skip("matplotlib font resolver unavailable")

    font = _load_font(20, bold=True)

    assert font.__class__.__name__ == "FreeTypeFont"


def test_family_tree_prefers_same_font_as_charts() -> None:
    try:
        from matplotlib import font_manager, rcParams
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"matplotlib unavailable: {exc}")

    families = rcParams.get("font.family") or ["sans-serif"]
    if not isinstance(families, list):
        families = [families]

    expected_path = font_manager.findfont(
        font_manager.FontProperties(family=families, weight="bold"),
        fallback_to_default=True,
    )

    assert _font_candidates(bold=True)[0] == expected_path


def test_split_text_runs_keeps_emoji_clusters_together() -> None:
    assert _split_text_runs("Родитель 👨‍👩‍👧 😀") == [
        ("Родитель ", False),
        ("👨‍👩‍👧", True),
        (" ", False),
        ("😀", True),
    ]


async def test_build_family_tree_image_accepts_emoji_labels() -> None:
    image_bytes = await build_family_tree_image(
        subject_label="😀 @BeykusY",
        parents=["👨 Папа", "👩 Мама"],
        step_parents=["🧑 Отчим"],
        spouse="💛 Партнёр",
        siblings=["😎 Сиблинг"],
        children=["👶 Ребёнок"],
        pets=["🐈 Кот"],
        grandparents=["🧓 Дедушка"],
    )

    image = Image.open(BytesIO(image_bytes))

    assert image.format == "PNG"


async def test_build_family_tree_image_escapes_special_characters() -> None:
    image_bytes = await build_family_tree_image(
        subject_label="<script>alert('Ego')</script> {{var}}",
        parents=["<b>Parent 1</b>", "Parent 2"],
        step_parents=["Step 1"],
        spouse="Partner & spouse",
        siblings=["Sibling <3"],
        children=["Child & co"],
        pets=["Pet <script></script>"],
        grandparents=["Grandparent <gp>"]
    )
    assert image_bytes is not None


async def test_build_family_tree_image_handles_diverse_texts() -> None:
    # composite emojis, CJK, RTL, empty/spaces, single long word
    image_bytes = await build_family_tree_image(
        subject_label="👨‍👩‍👧‍👦 🇺🇸 🏻",  # ZWJ emoji, flag, skin-tone modifier
        parents=["你好世界 (CJK)", "العربية (RTL)"],
        step_parents=["   ", ""],  # empty/whitespace names
        spouse="SupercalifragilisticexpialidociousWithoutAnySpaces",  # single long word
        siblings=[],
        children=[],
        pets=[],
        grandparents=[]
    )
    assert image_bytes is not None

