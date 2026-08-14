from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_admin_broadcast_composer_exposes_photo_and_reaction_builder() -> None:
    source = (ROOT / "frontend/src/pages/admin/ui/AdminPageView.tsx").read_text(encoding="utf-8")
    api = (ROOT / "frontend/src/pages/admin/api/admin-actions.ts").read_text(encoding="utf-8")

    assert 'type="file"' in source
    assert 'accept="image/jpeg,image/png"' in source
    assert "[reactions]" in source
    assert "tg-spoiler" in source
    assert "tg-emoji" in source
    assert "tg-time" in source
    assert "blockquote expandable" in source
    assert "language-python" in source
    assert "&amp;quot;" in source
    assert "media_mode" in api
    assert "FormData" in api

    fallback = (ROOT / "src/selara/web/templates/admin.html").read_text(encoding="utf-8")
    assert "tg-emoji" in fallback
    assert "tg-time" in fallback
    assert "blockquote expandable" in fallback


def test_broadcast_details_expose_delivery_mode_and_reactors() -> None:
    source = (ROOT / "frontend/src/pages/admin-broadcast/ui/BroadcastPageView.tsx").read_text(encoding="utf-8")
    types = (ROOT / "frontend/src/pages/admin-broadcast/model/types.ts").read_text(encoding="utf-8")

    assert "reaction_mode" in types
    assert "reactions:" in types
    assert "Реакции пользователей" in source
    assert "inline" in source
    assert "native" in source

    fallback = (ROOT / "src/selara/web/templates/admin_broadcast_detail.html").read_text(encoding="utf-8")
    assert "Ответы по вариантам" in fallback
    assert "Другие реакции" in fallback
    assert "custom_emoji" in fallback
    assert "paid" in fallback
