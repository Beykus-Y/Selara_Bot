from aiogram.types import WebAppInfo

from selara.core.config import Settings
from selara.presentation.handlers.private_panel import (
    _build_home_keyboard,
    _build_miniapp_url,
    _build_miniapp_webapp_url,
    decode_pm_callback,
    encode_pm_callback,
)


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


def test_build_miniapp_urls_split_chat_and_webapp_destinations() -> None:
    settings = Settings.model_construct(
        web_enabled=True,
        bot_username="selara_ru_bot",
        bot_name="Selara",
        web_domain="selarabot.duckdns.org",
        web_base_url="http://127.0.0.1:8080",
    )

    assert _build_miniapp_url(settings) == "https://t.me/selara_ru_bot"
    assert _build_miniapp_webapp_url(settings) == "https://selarabot.duckdns.org/miniapp/"


def test_build_home_keyboard_uses_web_app_button_when_available() -> None:
    markup = _build_home_keyboard(
        has_admin_groups=False,
        has_user_groups=False,
        miniapp_url="https://t.me/selara_ru_bot",
        miniapp_webapp_url="https://selarabot.duckdns.org/miniapp/",
        desktop_url=None,
    )

    row = markup.inline_keyboard[0]
    assert len(row) == 1
    button = row[0]
    assert button.text == "📱 Mini App"
    assert button.web_app == WebAppInfo(url="https://selarabot.duckdns.org/miniapp/")
    assert button.url is None
