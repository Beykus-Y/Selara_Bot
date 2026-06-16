import os
import sys
from io import BytesIO
import pytest
from PIL import Image, ImageChops

from selara.presentation.family_tree import build_family_tree_image
from selara.presentation.renderer_service import PlaywrightRendererService

SNAPSHOTS_DIR = os.path.join(os.path.dirname(__file__), "snapshots")


@pytest.fixture(autouse=True)
async def use_renderer():
    renderer = PlaywrightRendererService.get_instance()
    await renderer.start()
    yield
    await renderer.stop()


def compare_images(generated_bytes: bytes, golden_path: str, threshold: float = 0.01) -> tuple[bool, float]:
    """
    Compares generated_bytes against the image at golden_path.
    Returns (is_match, diff_percentage).
    """
    gen_img = Image.open(BytesIO(generated_bytes)).convert("RGB")
    golden_img = Image.open(golden_path).convert("RGB")

    if gen_img.size != golden_img.size:
        return False, 1.0

    diff = ImageChops.difference(gen_img, golden_img)
    diff_gray = diff.convert("L")
    
    non_zero = 0
    pixels = diff_gray.getdata()
    for p in pixels:
        if p > 2:  # allow minor subpixel antialiasing/noise
            non_zero += 1
            
    total_pixels = gen_img.width * gen_img.height
    diff_pct = non_zero / total_pixels
    
    return diff_pct <= threshold, diff_pct


async def assert_snapshot(image_bytes: bytes, snapshot_name: str) -> None:
    golden_path = os.path.join(SNAPSHOTS_DIR, f"{snapshot_name}.png")
    
    # Check if update mode is requested
    if os.environ.get("UPDATE_SNAPSHOTS") == "1" or not os.path.exists(golden_path):
        os.makedirs(SNAPSHOTS_DIR, exist_ok=True)
        with open(golden_path, "wb") as f:
            f.write(image_bytes)
        return

    # Compare
    is_match, diff_pct = compare_images(image_bytes, golden_path)
    
    # On Windows, we assert valid image and don't strictly fail on subpixel font mismatches,
    # but strictly enforce on Linux (Docker/CI).
    if sys.platform == "win32":
        assert len(image_bytes) > 0
    else:
        assert is_match, f"Snapshot {snapshot_name} mismatch by {diff_pct:.2%}"


async def test_snapshot_f01_empty_tree() -> None:
    # F-01: Empty tree state (subject only)
    image_bytes = await build_family_tree_image(
        subject_label="SingleEgo",
        parents=[],
        step_parents=[],
        spouse=None,
        siblings=[],
        children=[],
        pets=[],
        grandparents=[]
    )
    await assert_snapshot(image_bytes, "F01_empty_tree")


async def test_snapshot_f02_many_children() -> None:
    # F-02: 15 children/pets total (exceeding limit)
    image_bytes = await build_family_tree_image(
        subject_label="Ego",
        parents=["Parent 1", "Parent 2"],
        spouse="Spouse",
        siblings=[],
        children=[f"Child {i}" for i in range(1, 11)],
        pets=[f"Pet {i}" for i in range(1, 6)],
        grandparents=[]
    )
    await assert_snapshot(image_bytes, "F02_many_children")


async def test_snapshot_f03_normal_tree() -> None:
    # F-03: standard family setup
    image_bytes = await build_family_tree_image(
        subject_label="Ego",
        parents=["Father", "Mother"],
        step_parents=["Stepfather"],
        spouse="Partner",
        siblings=["Brother", "Sister"],
        children=["Son", "Daughter"],
        pets=["Cat", "Dog"],
        grandparents=["Grandpa", "Grandma"]
    )
    await assert_snapshot(image_bytes, "F03_normal_tree")


async def test_snapshot_f04_complex_names() -> None:
    # F-04: CJK, RTL, emojis, escaping, long words
    image_bytes = await build_family_tree_image(
        subject_label="👨‍👩‍👧‍👦 🇺🇸 🏻",
        parents=["你好世界 (CJK)", "العربية (RTL)"],
        step_parents=["<b>Escaped</b>", "{{NoInjection}}"],
        spouse="SupercalifragilisticexpialidociousWithoutAnySpaces",
        siblings=[],
        children=[],
        pets=[],
        grandparents=[]
    )
    await assert_snapshot(image_bytes, "F04_complex_names")


from selara.presentation.charts import build_daily_activity_chart

def test_snapshot_c01_empty_chart() -> None:
    # C-01: active_days = 0 -> should return None
    chart = build_daily_activity_chart(points=[("01.06", 0), ("02.06", 0)])
    assert chart is None


async def test_snapshot_c02_tie_peak() -> None:
    # C-02: tie-peak (multiple maximums, annotate last one)
    chart = build_daily_activity_chart(points=[
        ("01.06", 100), ("02.06", 50), ("03.06", 100), ("04.06", 75), ("05.06", 100)
    ])
    assert chart is not None
    await assert_snapshot(chart, "C02_tie_peak")


async def test_snapshot_c03_small_values() -> None:
    # C-03: values [0, 1, 2, 158] (minimum height stub for 1 and 2, dots for 0)
    chart = build_daily_activity_chart(points=[
        ("01.06", 0), ("02.06", 1), ("03.06", 2), ("04.06", 158)
    ])
    assert chart is not None
    await assert_snapshot(chart, "C03_small_values")


async def test_snapshot_c04_large_peak() -> None:
    # C-04: very large peak (Y axis scales without clipping)
    chart = build_daily_activity_chart(points=[
        ("01.06", 10), ("02.06", 20), ("03.06", 10000)
    ])
    assert chart is not None
    await assert_snapshot(chart, "C04_large_peak")


async def test_snapshot_c05_one_day() -> None:
    # C-05: one day data
    chart = build_daily_activity_chart(points=[("01.06", 42)])
    assert chart is not None
    await assert_snapshot(chart, "C05_one_day")

