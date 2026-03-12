from io import BytesIO

from PIL import Image

from selara.presentation.family_tree import build_family_tree_image


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
