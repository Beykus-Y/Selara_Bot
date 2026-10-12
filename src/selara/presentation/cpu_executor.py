from __future__ import annotations

import asyncio
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from typing import TypeVar

_T = TypeVar("_T")

# Pillow work runs on its own small pool. The default executor also serves DNS lookups, matplotlib and backups, so a long
# image job there would queue them behind it.
_CPU_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="cpu-offload")


async def run_in_cpu_executor(func: Callable[..., _T], /, *args: object, **kwargs: object) -> _T:
    return await asyncio.get_running_loop().run_in_executor(_CPU_EXECUTOR, partial(func, *args, **kwargs))
