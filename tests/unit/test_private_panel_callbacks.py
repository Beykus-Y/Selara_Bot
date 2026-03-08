from selara.presentation.handlers.private_panel import decode_pm_callback, encode_pm_callback


def test_pm_callback_encode_decode_roundtrip() -> None:
    data = encode_pm_callback("as", -100123, 2)
    decoded = decode_pm_callback(data)
    assert decoded is not None
    route, args = decoded
    assert route == "as"
    assert args == ["-100123", "2"]


def test_pm_callback_decode_rejects_invalid_payload() -> None:
    assert decode_pm_callback(None) is None
    assert decode_pm_callback("") is None
    assert decode_pm_callback("x:as:1:2") is None
