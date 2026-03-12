from io import BytesIO

import pytest
from PIL import Image

from selara.presentation.family_tree import _font_candidates, _load_font, build_family_tree_image


def test_build_family_tree_image_uses_dashboard_palette() -> None:
    image_bytes = build_family_tree_image(
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
    palette = {color for _, color in colors}

    assert image.format == "PNG"
    assert image.getpixel((0, 0)) == (7, 19, 31)
    assert (12, 29, 49) in palette
    assert (103, 232, 249) in palette
    assert (251, 191, 36) in palette
    assert (167, 139, 250) in palette
    assert (251, 113, 133) in palette


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
