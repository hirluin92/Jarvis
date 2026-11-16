"""A tiny in-memory broadcast of what Jarvis is doing, for the live screen (/jarvis)."""

import asyncio
import time
from collections import deque
from typing import Any


class EventBus:
    """Fan-out of small JSON events. Slow listeners lose old events instead of blocking."""

    def __init__(self, history: int = 30) -> None:
        self._listeners: set[asyncio.Queue] = set()
        self.recent: deque[dict[str, Any]] = deque(maxlen=history)

    def publish(self, kind: str, **data: Any) -> None:
        event = {"type": kind, "at": time.time(), **data}
        self.recent.append(event)
        for q in list(self._listeners):
            if q.full():
                q.get_nowait()
            q.put_nowait(event)

    def listen(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=100)
        self._listeners.add(q)
        return q

    def unlisten(self, q: asyncio.Queue) -> None:
        self._listeners.discard(q)
