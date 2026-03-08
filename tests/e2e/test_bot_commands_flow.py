import pytest


@pytest.mark.e2e
def test_e2e_placeholder() -> None:
    pytest.skip("E2E requires Telegram sandbox and bot token")
