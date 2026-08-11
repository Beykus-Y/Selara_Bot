from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from selara.infrastructure.http.gacha_client import HttpGachaClient


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["pull", "purchase", "sell"])
async def test_gacha_client_sends_service_token_for_mutating_user_calls(operation: str) -> None:
    client = HttpGachaClient(
        base_url="http://gacha.local",
        timeout_seconds=5.0,
        service_token="service-secret",
    )
    client._request = AsyncMock(return_value={})

    with pytest.raises(ValidationError):
        if operation == "pull":
            await client.pull(user_id=10, username="user", banner="genshin")
        elif operation == "purchase":
            await client.purchase_pull(user_id=10, username="user", banner="genshin")
        else:
            await client.sell_pull(user_id=10, pull_id=99)

    assert client._request.await_args.kwargs["headers"] == {
        "X-Gacha-Service-Token": "service-secret",
    }
