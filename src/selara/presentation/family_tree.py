from __future__ import annotations

import html
import re
from io import BytesIO
import unicodedata
from typing import Any, Sequence

from selara.presentation.font_support import resolve_emoji_font_paths, resolve_matplotlib_font_path
from selara.presentation.renderer_service import PlaywrightRendererService
from selara.presentation.charts import (
    _ACCENT_CYAN,
    _ACCENT_GOLD,
    _ACCENT_ROSE,
    _ACCENT_VIOLET,
    _FIGURE_BG,
    _GRID,
    _PANEL_BG,
    _TEXT_MAIN,
    _TEXT_MUTED,
)

# Pillow downscale dependency
from PIL import Image

_EMOJI_RANGES = (
    (0x1F1E6, 0x1F1FF),
    (0x1F300, 0x1F5FF),
    (0x1F600, 0x1F64F),
    (0x1F680, 0x1F6FF),
    (0x1F700, 0x1F77F),
    (0x1F780, 0x1F7FF),
    (0x1F800, 0x1F8FF),
    (0x1F900, 0x1F9FF),
    (0x1FA70, 0x1FAFF),
    (0x2600, 0x26FF),
    (0x2700, 0x27BF),
)

# Kept for backward compatibility with existing tests
def _font_candidates(*, bold: bool) -> tuple[str, ...]:
    resolved = resolve_matplotlib_font_path(bold=bold)
    if resolved is None:
        return ()
    return (resolved,)

def _emoji_font_candidates() -> tuple[str, ...]:
    return resolve_emoji_font_paths()

def _load_font(size: int, *, bold: bool = False):
    from PIL import ImageFont
    for candidate in _font_candidates(bold=bold):
        try:
            return ImageFont.truetype(candidate, size=size)
        except OSError:
            continue
    return ImageFont.load_default()

_EMOJI_FIXED_SIZES = (109, 128, 96, 72, 64)

def _load_emoji_font(size: int):
    from PIL import ImageFont
    load_sizes = (size, *[candidate for candidate in _EMOJI_FIXED_SIZES if candidate != size])
    for candidate in _emoji_font_candidates():
        for load_size in load_sizes:
            try:
                return ImageFont.truetype(candidate, size=load_size), load_size
            except OSError:
                continue
    return None

def _is_emoji_char(value: str) -> bool:
    if not value:
        return False
    codepoint = ord(value)
    return any(start <= codepoint <= end for start, end in _EMOJI_RANGES)

def _is_regional_indicator(value: str) -> bool:
    return bool(value) and 0x1F1E6 <= ord(value) <= 0x1F1FF

def _is_emoji_modifier(value: str) -> bool:
    return bool(value) and 0x1F3FB <= ord(value) <= 0x1F3FF

def _is_variation_selector(value: str) -> bool:
    return value in {"\ufe0e", "\ufe0f"}

def _is_keycap_base(value: str) -> bool:
    return value in set("0123456789#*")

def _is_emoji_start(text: str, index: int) -> bool:
    value = text[index]
    if _is_emoji_char(value) or _is_regional_indicator(value):
        return True
    if _is_keycap_base(value):
        next_value = text[index + 1] if index + 1 < len(text) else ""
        after_next = text[index + 2] if index + 2 < len(text) else ""
        return next_value == "\ufe0f" or next_value == "\u20e3" or after_next == "\u20e3"
    return False

def _consume_emoji_cluster(text: str, start: int) -> tuple[str, int]:
    cluster = [text[start]]
    index = start + 1

    if _is_regional_indicator(cluster[0]) and index < len(text) and _is_regional_indicator(text[index]):
        cluster.append(text[index])
        index += 1

    while index < len(text):
        value = text[index]
        if _is_variation_selector(value) or value == "\u20e3" or _is_emoji_modifier(value) or unicodedata.combining(value):
            cluster.append(value)
            index += 1
            continue
        if value == "\u200d" and index + 1 < len(text):
            cluster.append(value)
            cluster.append(text[index + 1])
            index += 2
            continue
        break
    return "".join(cluster), index

def _split_text_runs(text: str) -> list[tuple[str, bool]]:
    runs: list[tuple[str, bool]] = []
    index = 0
    while index < len(text):
        if _is_emoji_start(text, index):
            cluster, index = _consume_emoji_cluster(text, index)
            runs.append((cluster, True))
            continue
        start = index
        index += 1
        while index < len(text) and not _is_emoji_start(text, index):
            index += 1
        runs.append((text[start:index], False))
    return runs


# New rendering logic
def escape_html(text: str | None) -> str:
    if not text:
        return "(без имени)"
    normalized = " ".join(text.split()).strip()
    if not normalized:
        return "(без имени)"
    return html.escape(normalized)

def is_aggregate(label: str) -> bool:
    return bool(re.match(r"^\+\d+\s*(еще|ещё|more)", label, re.IGNORECASE))

def chunk_list(lst: list[Any], chunk_size: int) -> list[list[Any]]:
    return [lst[i : i + chunk_size] for i in range(0, len(lst), chunk_size)]

def truncate_list(labels: list[str], limit: int) -> list[str]:
    if len(labels) <= limit:
        return labels
    visible = labels[:limit - 1]
    remaining = len(labels) - len(visible)
    visible.append(f"+{remaining} еще")
    return visible

def render_card(role_title: str, name_label: str, accent_color: str, extra_class: str = "") -> str:
    escaped_name = escape_html(name_label)
    if is_aggregate(name_label):
        return f"""
        <div class="card aggregate {extra_class}" style="--border-color: var(--text-muted)">
            <div class="card-name"><span dir="auto">{escaped_name}</span></div>
        </div>
        """
    return f"""
    <div class="card {extra_class}" style="--border-color: {accent_color}">
        <div class="card-dot" style="background-color: {accent_color}"></div>
        <div class="card-subtitle">{role_title}</div>
        <div class="card-name"><span dir="auto">{escaped_name}</span></div>
    </div>
    """

def downscale_if_needed(image_bytes: bytes, max_side: int = 1280) -> bytes:
    im = Image.open(BytesIO(image_bytes))
    w, h = im.size
    if max(w, h) > max_side:
        scale = max_side / max(w, h)
        new_w = int(round(w * scale))
        new_h = int(round(h * scale))
        im = im.resize((new_w, new_h), Image.Resampling.LANCZOS)
    buffer = BytesIO()
    im.save(buffer, format="PNG")
    return buffer.getvalue()

async def build_family_tree_image(
    *,
    subject_label: str,
    parents: list[str],
    step_parents: list[str] | None = None,
    spouse: str | None,
    siblings: list[str] | None = None,
    children: list[str],
    pets: list[str],
    grandparents: list[str] | None = None,
) -> bytes:
    # 1. Compute stats row metrics (based on total deduplicated counts before truncation)
    total_parents_count = len(parents) + len(step_parents or [])
    total_spouse_count = 1 if spouse else 0
    total_children_count = len(children)
    total_pets_count = len(pets)

    # 2. Truncate using display limits
    step_parents = step_parents or []
    siblings = siblings or []
    grandparents = grandparents or []

    truncated_parents = truncate_list(parents, 2)
    truncated_step_parents = truncate_list(step_parents, 2)
    truncated_siblings = truncate_list(siblings, 5)
    truncated_children = truncate_list(children, 8)
    truncated_pets = truncate_list(pets, 6)
    truncated_grandparents = truncate_list(grandparents, 4)

    # 3. Generate stats row HTML
    stats_pills = [
        (f"Родителей: {total_parents_count}", "#6FA8FF"),
        (f"Партнёров: {total_spouse_count}", "#F5B544"),
        (f"Детей: {total_children_count}", "#8B7CF6"),
        (f"Питомцев: {total_pets_count}", "#FF6B8A"),
    ]
    stats_html = "".join(
        f'<div class="pill" style="--border-color: {color}">{escape_html(text)}</div>'
        for text, color in stats_pills
    )

    # 4. Generate content HTML
    content_parts = []

    # A. Grandparents zone
    if truncated_grandparents:
        grandparent_rows = chunk_list(truncated_grandparents, 4)
        gp_html = []
        gp_html.append('<div class="zone grandparents-zone">')
        gp_html.append('  <div class="section-title">Предки</div>')
        gp_html.append('  <div style="display: flex; flex-direction: column; align-items: center; gap: 16px; width: 100%;">')
        for row in grandparent_rows:
            gp_html.append('    <div class="cards-row">')
            for name in row:
                gp_html.append(f'      {render_card("Предок", name, "var(--accent-ancestor)")}')
            gp_html.append('    </div>')
        gp_html.append('  </div>')
        gp_html.append('</div>')
        content_parts.append("\n".join(gp_html))

    # B. Parents zone
    parents_list = [("parent", p) for p in truncated_parents] + [("step_parent", sp) for sp in truncated_step_parents]
    if parents_list:
        parents_rows = chunk_list(parents_list, 4)
        p_html = []
        p_html.append('<div class="zone parents-zone">')
        p_html.append('  <div class="section-title">Родители</div>')
        p_html.append('  <div class="parents-wrapper">')
        for row_idx, row in enumerate(parents_rows):
            p_html.append('    <div style="display: flex; flex-direction: column; align-items: center; width: fit-content;">')
            p_html.append('      <div class="cards-row" style="width: fit-content;">')
            for rel_type, name in row:
                accent = "var(--accent-parent)" if rel_type == "parent" else "var(--accent-step)"
                role = "Родитель" if rel_type == "parent" else "Отчим/мачеха"
                p_html.append('        <div class="card-wrapper">')
                p_html.append(f'          {render_card(role, name, accent)}')
                p_html.append('          <div class="vertical-branch parent-branch"></div>')
                p_html.append('        </div>')
            p_html.append('      </div>')
            p_html.append('      <div class="horizontal-branch parent-branch" style="width: calc(100% - 200px);"></div>')
            p_html.append('    </div>')
        p_html.append('    <div class="vertical-trunk parent-branch"></div>')
        p_html.append('  </div>')
        p_html.append('</div>')
        content_parts.append("\n".join(p_html))
    else:
        # Fallback empty title / text
        content_parts.append('<div class="zone parents-zone"><div class="section-title">Родители не заданы</div></div>')

    # C. Ego and Spouse zone
    ego_html = []
    ego_html.append('<div class="zone ego-zone">')
    ego_html.append('  <div class="ego-spouse-container">')
    ego_html.append(f'    {render_card("Центр династии", subject_label, "var(--accent-center)", "ego")}')
    if spouse:
        ego_html.append('    <div class="spouse-connector"></div>')
        ego_html.append(f'    {render_card("Супруг(а)", spouse, "var(--accent-spouse)")}')
    ego_html.append('  </div>')
    ego_html.append('</div>')
    content_parts.append("\n".join(ego_html))

    # D. Siblings zone
    if truncated_siblings:
        sib_rows = chunk_list(truncated_siblings, 4)
        sib_html = []
        sib_html.append('<div class="zone siblings-zone">')
        sib_html.append('  <div class="section-title">Братья и сёстры</div>')
        sib_html.append('  <div style="display: flex; flex-direction: column; align-items: center; gap: 16px; width: 100%;">')
        for row in sib_rows:
            sib_html.append('    <div class="cards-row">')
            for name in row:
                sib_html.append(f'      {render_card("Брат/сестра", name, "var(--accent-sibling)")}')
            sib_html.append('    </div>')
        sib_html.append('  </div>')
        sib_html.append('</div>')
        content_parts.append("\n".join(sib_html))

    # E. Children and Pets zone
    lower_list = [("child", ch) for ch in truncated_children] + [("pet", pet) for pet in truncated_pets]
    if lower_list:
        lower_rows = chunk_list(lower_list, 4)
        ch_html = []
        ch_html.append('<div class="zone children-zone">')
        ch_html.append('  <div class="section-title">Дети и питомцы</div>')
        ch_html.append('  <div class="children-wrapper">')
        for row_idx, row in enumerate(lower_rows):
            if row_idx > 0:
                ch_html.append('    <div class="vertical-trunk child-branch middle-trunk"></div>')
            ch_html.append('    <div class="children-row-container">')
            ch_html.append('      <div class="vertical-trunk child-branch"></div>')
            ch_html.append('      <div style="display: flex; flex-direction: column; align-items: center; width: fit-content;">')
            ch_html.append('        <div class="horizontal-branch child-branch" style="width: calc(100% - 200px);"></div>')
            ch_html.append('        <div class="cards-row" style="width: fit-content; margin-top: 0;">')
            for item_type, name in row:
                accent = "var(--accent-child)" if item_type == "child" else "var(--accent-pet)"
                role = "Ребёнок" if item_type == "child" else "Питомец"
                ch_html.append('          <div class="card-wrapper">')
                ch_html.append('            <div class="vertical-branch child-branch"></div>')
                ch_html.append(f'            {render_card(role, name, accent)}')
                ch_html.append('          </div>')
            ch_html.append('        </div>')
            ch_html.append('      </div>')
            ch_html.append('    </div>')
        ch_html.append('  </div>')
        ch_html.append('</div>')
        content_parts.append("\n".join(ch_html))

    content_html = "\n".join(content_parts)

    html_template = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
:root {{
  --bg-deep:        #0A0E1A;
  --bg-panel:       #111729;
  --card-bg-top:    #1A2238;
  --card-bg-bot:    #141B30;
  --card-border:    #2A3550;
  --text-primary:   #EAF0FF;
  --text-muted:     #8593B0;
  --grid:           rgba(255,255,255,0.06);

  --accent-center:  #38E1FF;
  --accent-spouse:  #F5B544;
  --accent-parent:  #6FA8FF;
  --accent-step:    #B0A0FF;
  --accent-sibling: #3DD6C0;
  --accent-child:   #8B7CF6;
  --accent-pet:     #FF6B8A;
  --accent-ancestor:#5A6B92;
}}

body {{
  background-color: var(--bg-deep);
  color: var(--text-primary);
  font-family: 'Nunito', 'Inter', 'Noto Sans', 'Noto Sans CJK JP', 'Noto Color Emoji', sans-serif;
  margin: 0;
  padding: 48px;
  display: flex;
  flex-direction: column;
  align-items: center;
  box-sizing: border-box;
  width: 1000px;
  min-height: 500px;
}}

.header {{
  width: 100%;
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 24px;
}}
.header-title {{
  font-size: 28px;
  font-weight: 800;
  color: var(--text-primary);
}}
.header-subtitle {{
  font-size: 14px;
  color: var(--text-muted);
  font-weight: 600;
}}

.stats-row {{
  display: flex;
  gap: 12px;
  margin-bottom: 32px;
  width: 100%;
}}
.pill {{
  padding: 6px 14px;
  border-radius: 999px;
  font-size: 14px;
  font-weight: 700;
  background-color: var(--bg-panel);
  border: 1.5px solid var(--border-color);
  color: var(--text-primary);
}}

.zone {{
  display: flex;
  flex-direction: column;
  align-items: center;
  width: 100%;
  margin-bottom: 28px;
}}
.section-title {{
  font-size: 14px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--text-muted);
  margin-bottom: 16px;
  text-align: center;
  width: 100%;
}}

.cards-row {{
  display: flex;
  justify-content: center;
  gap: 16px;
  width: 100%;
}}

.card-wrapper {{
  display: flex;
  flex-direction: column;
  align-items: center;
  position: relative;
}}

.card {{
  width: 200px;
  height: 86px;
  border-radius: 14px;
  background: linear-gradient(180deg, var(--card-bg-top) 0%, var(--card-bg-bot) 100%);
  border: 2px solid var(--border-color);
  padding: 12px 14px;
  box-sizing: border-box;
  position: relative;
  display: flex;
  flex-direction: column;
  justify-content: center;
}}
.card.ego {{
  border-width: 3px;
  box-shadow: 0 0 12px rgba(56, 225, 255, 0.35);
}}
.card.aggregate {{
  background: var(--bg-panel);
  border-style: dashed;
  text-align: center;
  align-items: center;
}}
.card.aggregate .card-name {{
  color: var(--text-muted);
}}
.card-subtitle {{
  font-size: 10px;
  font-weight: 600;
  color: var(--text-muted);
  margin-bottom: 4px;
  text-transform: uppercase;
  letter-spacing: 0.02em;
}}
.card-name {{
  font-size: 15px;
  font-weight: 700;
  color: var(--text-primary);
  line-height: 1.25;
  word-break: break-word;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  text-overflow: ellipsis;
}}
.card-dot {{
  position: absolute;
  top: 10px;
  right: 12px;
  width: 8px;
  height: 8px;
  border-radius: 50%;
}}

/* Branch Line CSS */
.parents-wrapper {{
  display: flex;
  flex-direction: column;
  align-items: center;
  width: 100%;
}}
.vertical-branch.parent-branch {{
  width: 2px;
  height: 16px;
  background-color: var(--accent-parent);
}}
.horizontal-branch.parent-branch {{
  height: 2px;
  background-color: var(--accent-parent);
  margin-bottom: 0;
}}
.vertical-trunk.parent-branch {{
  width: 2px;
  height: 24px;
  background-color: var(--accent-parent);
}}

.ego-spouse-container {{
  display: flex;
  align-items: center;
  justify-content: center;
}}
.spouse-connector {{
  width: 32px;
  height: 0;
  border-top: 2px dashed var(--accent-spouse);
}}

.children-wrapper {{
  display: flex;
  flex-direction: column;
  align-items: center;
  width: 100%;
}}
.children-row-container {{
  display: flex;
  flex-direction: column;
  align-items: center;
  width: 100%;
}}
.vertical-trunk.child-branch {{
  width: 2px;
  height: 24px;
  background-color: var(--accent-child);
}}
.horizontal-branch.child-branch {{
  height: 2px;
  background-color: var(--accent-child);
}}
.vertical-branch.child-branch {{
  width: 2px;
  height: 16px;
  background-color: var(--accent-child);
}}
.vertical-trunk.child-branch.middle-trunk {{
  height: 32px;
}}
</style>
</head>
<body>
<div class="header">
  <div class="header-title">Семья и династия</div>
  <div class="header-subtitle">Selara • family graph</div>
</div>

<div class="stats-row">
  {stats_html}
</div>

{content_html}

</body>
</html>
"""

    renderer = PlaywrightRendererService.get_instance()
    image_bytes = await renderer.render_html(html_template, width=1000, height=800)
    
    # Target @2x is already rendered in renderer. Now downscale to <= 1280px on the longest side
    return downscale_if_needed(image_bytes, max_side=1280)
