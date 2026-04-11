"""Async event bus connecting observation → decisions.

asyncio.Queue-based pub/sub for decoupled communication between the
observation engine (file changes, pattern detection) and the decision
engine (contradiction checks, audit logging).

From SPEC: Lattice observation events feed into Axiom Hub decision pipeline.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Coroutine

logger = logging.getLogger(__name__)


class EventType(str, Enum):
    """Observable events emitted by the system."""

    FILE_CHANGED = "file_changed"
    PATTERN_DETECTED = "pattern_detected"
    DEPENDENCY_UPDATED = "dependency_updated"
    LLM_CALL_OBSERVED = "llm_call_observed"
    DECISION_RECORDED = "decision_recorded"
    CONTRADICTION_DETECTED = "contradiction_detected"


@dataclass
class Event:
    """A single event on the bus."""

    event_type: EventType
    payload: dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    source: str = ""


# Subscriber type: async callable that receives an Event
Subscriber = Callable[[Event], Coroutine[Any, Any, None]]


class EventBus:
    """asyncio.Queue-based event bus with topic-based subscriptions."""

    def __init__(self, maxsize: int = 1000) -> None:
        self._queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=maxsize)
        self._subscribers: dict[EventType, list[Subscriber]] = {}
        self._running = False
        self._consumer_task: asyncio.Task[None] | None = None

    def subscribe(self, event_type: EventType, handler: Subscriber) -> None:
        """Register a handler for a specific event type."""
        self._subscribers.setdefault(event_type, []).append(handler)

    async def publish(self, event: Event) -> None:
        """Enqueue an event for async delivery."""
        await self._queue.put(event)

    async def start(self) -> None:
        """Start the consumer loop."""
        self._running = True
        self._consumer_task = asyncio.create_task(self._consume())

    async def stop(self) -> None:
        """Stop the consumer loop and drain remaining events."""
        self._running = False
        if self._consumer_task:
            self._consumer_task.cancel()
            try:
                await self._consumer_task
            except asyncio.CancelledError:
                pass
            self._consumer_task = None

    async def _consume(self) -> None:
        """Process events from the queue and dispatch to subscribers."""
        while self._running:
            try:
                event = await asyncio.wait_for(self._queue.get(), timeout=0.5)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break

            handlers = self._subscribers.get(event.event_type, [])
            for handler in handlers:
                try:
                    await handler(event)
                except Exception:
                    logger.exception(
                        "Handler %s failed for event %s",
                        handler.__name__,
                        event.event_type.value,
                    )
            self._queue.task_done()
