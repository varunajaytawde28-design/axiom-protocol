"""Tests for async event bus."""

from __future__ import annotations

import asyncio

import pytest

from vt_protocol.events import Event, EventBus, EventType


class TestEventBus:
    @pytest.fixture
    def bus(self) -> EventBus:
        return EventBus()

    async def test_publish_subscribe(self, bus: EventBus) -> None:
        received: list[Event] = []

        async def handler(event: Event) -> None:
            received.append(event)

        bus.subscribe(EventType.FILE_CHANGED, handler)
        await bus.start()

        event = Event(event_type=EventType.FILE_CHANGED, payload={"path": "foo.py"})
        await bus.publish(event)

        # Give consumer time to process
        await asyncio.sleep(0.1)
        await bus.stop()

        assert len(received) == 1
        assert received[0].payload["path"] == "foo.py"

    async def test_multiple_subscribers(self, bus: EventBus) -> None:
        count_a = 0
        count_b = 0

        async def handler_a(event: Event) -> None:
            nonlocal count_a
            count_a += 1

        async def handler_b(event: Event) -> None:
            nonlocal count_b
            count_b += 1

        bus.subscribe(EventType.DECISION_RECORDED, handler_a)
        bus.subscribe(EventType.DECISION_RECORDED, handler_b)
        await bus.start()

        await bus.publish(Event(event_type=EventType.DECISION_RECORDED))
        await asyncio.sleep(0.1)
        await bus.stop()

        assert count_a == 1
        assert count_b == 1

    async def test_topic_isolation(self, bus: EventBus) -> None:
        received: list[EventType] = []

        async def handler(event: Event) -> None:
            received.append(event.event_type)

        bus.subscribe(EventType.FILE_CHANGED, handler)
        await bus.start()

        await bus.publish(Event(event_type=EventType.FILE_CHANGED))
        await bus.publish(Event(event_type=EventType.LLM_CALL_OBSERVED))
        await asyncio.sleep(0.1)
        await bus.stop()

        # Only FILE_CHANGED should be received
        assert received == [EventType.FILE_CHANGED]

    async def test_handler_error_does_not_crash_bus(self, bus: EventBus) -> None:
        good_received: list[Event] = []

        async def bad_handler(event: Event) -> None:
            raise RuntimeError("boom")

        async def good_handler(event: Event) -> None:
            good_received.append(event)

        bus.subscribe(EventType.PATTERN_DETECTED, bad_handler)
        bus.subscribe(EventType.PATTERN_DETECTED, good_handler)
        await bus.start()

        await bus.publish(Event(event_type=EventType.PATTERN_DETECTED))
        await asyncio.sleep(0.1)
        await bus.stop()

        # Good handler still receives the event
        assert len(good_received) == 1

    async def test_multiple_events(self, bus: EventBus) -> None:
        received: list[Event] = []

        async def handler(event: Event) -> None:
            received.append(event)

        bus.subscribe(EventType.DEPENDENCY_UPDATED, handler)
        await bus.start()

        for i in range(5):
            await bus.publish(
                Event(event_type=EventType.DEPENDENCY_UPDATED, payload={"idx": i})
            )
        await asyncio.sleep(0.2)
        await bus.stop()

        assert len(received) == 5

    async def test_stop_is_idempotent(self, bus: EventBus) -> None:
        await bus.start()
        await bus.stop()
        await bus.stop()  # Should not raise

    def test_event_types_exist(self) -> None:
        assert EventType.FILE_CHANGED.value == "file_changed"
        assert EventType.PATTERN_DETECTED.value == "pattern_detected"
        assert EventType.DEPENDENCY_UPDATED.value == "dependency_updated"
        assert EventType.LLM_CALL_OBSERVED.value == "llm_call_observed"
        assert EventType.DECISION_RECORDED.value == "decision_recorded"
        assert EventType.CONTRADICTION_DETECTED.value == "contradiction_detected"

    def test_event_has_timestamp(self) -> None:
        event = Event(event_type=EventType.FILE_CHANGED)
        assert event.timestamp is not None

    def test_event_has_source(self) -> None:
        event = Event(event_type=EventType.FILE_CHANGED, source="observation")
        assert event.source == "observation"
