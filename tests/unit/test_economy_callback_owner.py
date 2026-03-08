from selara.presentation.handlers.economy import _extract_owner_from_parts, _with_owner_suffix


def test_owner_suffix_roundtrip() -> None:
    encoded = _with_owner_suffix("eco:dash:l", 42)
    parts, owner_user_id = _extract_owner_from_parts(encoded.split(":"))
    assert ":".join(parts) == "eco:dash:l"
    assert owner_user_id == 42


def test_owner_suffix_compat_without_owner() -> None:
    parts, owner_user_id = _extract_owner_from_parts("eco:dash:l".split(":"))
    assert parts == ["eco", "dash", "l"]
    assert owner_user_id is None
