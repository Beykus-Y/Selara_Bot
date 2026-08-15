from __future__ import annotations

import json
from pathlib import Path

from selara.web.rendering import create_template_environment


def test_family_graph_json_cannot_break_out_of_script_tag() -> None:
    template_dir = Path(__file__).parents[2] / "src" / "selara" / "web" / "templates"
    environment = create_template_environment(template_dir=template_dir)
    malicious_label = "</script><script>globalThis.pwned=true</script>"
    nodes = [
        {
            "id": 1,
            "label": malicious_label,
            "href": "/app/family/-100?user_id=1",
            "role": "subject",
        }
    ]

    rendered = environment.get_template("family.html").render(
        page_title="Family",
        page_name="family",
        top_links=[],
        show_logout=False,
        chat_title="Test",
        focus_user_id=1,
        focus_label="Test",
        chat_section_links=[],
        bundle_summary=[],
        family_nodes=nodes,
        family_edges=[],
        family_nodes_json=json.dumps(nodes, ensure_ascii=False),
        family_edges_json="[]",
    )

    script = rendered.split("const nodes = ", 1)[1].split(";", 1)[0]
    assert "</script>" not in script.lower()
    assert "\\u003c/script\\u003e" in script.lower()


def test_family_graph_never_injects_member_labels_through_inner_html() -> None:
    template_path = Path(__file__).parents[2] / "src" / "selara" / "web" / "templates" / "family.html"
    template = template_path.read_text(encoding="utf-8")

    assert "innerHTML" not in template


def test_audit_timeline_escapes_content_and_does_not_render_raw_meta() -> None:
    template_dir = Path(__file__).parents[2] / "src" / "selara" / "web" / "templates"
    environment = create_template_environment(template_dir=template_dir)
    malicious = '<img src=x onerror="globalThis.pwned=true">'

    rendered = environment.get_template("audit.html").render(
        page_title="Audit",
        page_name="audit",
        top_links=[],
        show_logout=False,
        flash=None,
        error=None,
        chat_title="Test",
        chat_id=123,
        chat_section_links=[],
        audit_total_count=1,
        audit_shown_count=1,
        audit_system_count=0,
        audit_filters={"q": malicious, "category": "all", "actor": "all"},
        audit_filter_errors=[],
        audit_load_error=None,
        audit_category_options=[{"value": "all", "label": "Все события"}],
        audit_actor_options=[{"value": "all", "label": "Все инициаторы"}],
        audit_reset_href="/app/chat/123/audit",
        audit_groups=[
            {
                "date_label": "15 августа 2026",
                "rows": [
                    {
                        "event_id": 1,
                        "when": "15.08.2026 10:00 UTC",
                        "time_label": "10:00",
                        "action": "custom_event",
                        "action_label": "Другое событие",
                        "category_code": "other",
                        "category_label": "Другое",
                        "tone": "neutral",
                        "description": malicious,
                        "actor": "system",
                        "actor_label": "Система",
                        "target": "—",
                        "target_label": "Без цели",
                        "has_target": False,
                    }
                ],
            }
        ],
    )

    assert malicious not in rendered
    assert "&lt;img" in rendered
    assert "meta_json" not in rendered
    assert "must-not-be-rendered" not in rendered
