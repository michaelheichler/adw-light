from __future__ import annotations

import contextvars
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from typing import TypeVar

MAX_WORKERS = 12
Result = TypeVar("Result")


def gathered(calls: Sequence[Callable[[], Result]]) -> list[Result]:
    if len(calls) < 2:
        return [call() for call in calls]
    with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(calls))) as pool:
        futures = [pool.submit(contextvars.copy_context().run, call) for call in calls]
        return [future.result() for future in futures]


def attempted(call: Callable[[], Result], errors: tuple[type[BaseException], ...]) -> Callable[[], Result | BaseException]:
    def run() -> Result | BaseException:
        try:
            return call()
        except errors as error:
            return error

    return run
