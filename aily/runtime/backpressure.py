from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import AsyncIterator


@dataclass
class _LimiterState:
    provider: str
    max_concurrency: int
    loop: asyncio.AbstractEventLoop
    semaphore: asyncio.Semaphore
    in_flight: int = 0
    queued: int = 0
    completed: int = 0
    failed: int = 0
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class ProviderBackpressure:
    """Process-wide provider concurrency guard.

    LLMClient instances already have local semaphores. This registry adds the
    missing global budget so separate schedulers cannot overload the same
    provider by each constructing its own client.
    """

    def __init__(self) -> None:
        self._states: dict[str, _LimiterState] = {}
        self._registry_lock: asyncio.Lock | None = None
        self._registry_loop: asyncio.AbstractEventLoop | None = None

    def _lock_for_current_loop(self) -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        if self._registry_lock is None or self._registry_loop is not loop:
            self._registry_lock = asyncio.Lock()
            self._registry_loop = loop
        return self._registry_lock

    async def _state_for(self, provider: str, max_concurrency: int) -> _LimiterState:
        normalized = (provider or "unknown").strip().lower() or "unknown"
        safe_max = max(1, int(max_concurrency))
        loop = asyncio.get_running_loop()
        async with self._lock_for_current_loop():
            state = self._states.get(normalized)
            if state is None or state.loop is not loop or state.max_concurrency != safe_max:
                state = _LimiterState(
                    provider=normalized,
                    max_concurrency=safe_max,
                    loop=loop,
                    semaphore=asyncio.Semaphore(safe_max),
                )
                self._states[normalized] = state
            return state

    @asynccontextmanager
    async def limit(self, provider: str, max_concurrency: int) -> AsyncIterator[None]:
        state = await self._state_for(provider, max_concurrency)
        async with state.lock:
            state.queued += 1
        await state.semaphore.acquire()
        async with state.lock:
            state.queued = max(0, state.queued - 1)
            state.in_flight += 1
        try:
            yield
        except Exception:
            async with state.lock:
                state.failed += 1
            raise
        else:
            async with state.lock:
                state.completed += 1
        finally:
            async with state.lock:
                state.in_flight = max(0, state.in_flight - 1)
            state.semaphore.release()

    async def snapshot(self) -> dict[str, dict[str, int]]:
        async with self._lock_for_current_loop():
            states = list(self._states.values())
        payload: dict[str, dict[str, int]] = {}
        for state in states:
            async with state.lock:
                payload[state.provider] = {
                    "max_concurrency": state.max_concurrency,
                    "in_flight": state.in_flight,
                    "queued": state.queued,
                    "completed": state.completed,
                    "failed": state.failed,
                }
        return payload


provider_backpressure = ProviderBackpressure()
