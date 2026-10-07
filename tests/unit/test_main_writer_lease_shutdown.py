"""Process shutdown around the game writer lease watch (issue #87).

The lease watch never ends on its own. It must not keep the process alive once
its services have ended, and it must still end them when the lease is lost.
"""

import asyncio

import pytest

from selara.main import _run_services
from selara.presentation.game_state import GameWriterLeaseHeldError


async def _service_that_ends() -> None:
    return None


async def _service_that_runs() -> None:
    await asyncio.Event().wait()


async def _lease_watch_that_waits() -> None:
    await asyncio.Event().wait()


async def _lease_watch_that_is_lost() -> None:
    raise GameWriterLeaseHeldError("taken over by another instance")


@pytest.mark.asyncio
async def test_clean_shutdown_does_not_wait_for_the_lease_watch() -> None:
    await asyncio.wait_for(
        _run_services(
            _service_that_ends(),
            _service_that_ends(),
            lease_watch=_lease_watch_that_waits(),
        ),
        timeout=5,
    )


@pytest.mark.asyncio
async def test_a_lost_lease_ends_the_services_that_are_still_running() -> None:
    with pytest.raises(ExceptionGroup) as raised:
        await asyncio.wait_for(
            _run_services(
                _service_that_runs(),
                lease_watch=_lease_watch_that_is_lost(),
            ),
            timeout=5,
        )
    assert any(isinstance(error, GameWriterLeaseHeldError) for error in raised.value.exceptions)
