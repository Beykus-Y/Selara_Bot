from __future__ import annotations

import threading

import pytest

from selara.presentation.cpu_executor import run_in_cpu_executor


@pytest.mark.asyncio
async def test_run_in_cpu_executor_returns_result_from_a_worker_thread() -> None:
    """Pillow jobs must not run on the event loop thread (a render froze every update for its whole duration)."""
    seen_threads: list[threading.Thread] = []

    def work(value: int, *, scale: int) -> int:
        seen_threads.append(threading.current_thread())
        return value * scale

    assert await run_in_cpu_executor(work, 3, scale=4) == 12
    assert len(seen_threads) == 1
    assert seen_threads[0] is not threading.main_thread()
