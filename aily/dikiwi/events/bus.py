"""Event bus implementation for DIKIWI.

Provides async, decoupled communication between stages.
Supports both in-memory and Redis backends.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from abc import ABC, abstractmethod
from collections import defaultdict
from typing import TYPE_CHECKING, Any, Callable, TypeVar

if TYPE_CHECKING:
    from aily.dikiwi.events.models import Event

logger = logging.getLogger(__name__)

T = TypeVar("T", bound="Event")

EventHandler = Callable[["Event"], Any]

# Redis configuration from environment
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
REDIS_STREAM_KEY = os.getenv("REDIS_STREAM_KEY", "dikiwi:events")
REDIS_CONSUMER_GROUP = os.getenv("REDIS_CONSUMER_GROUP", "dikiwi:consumers")


class EventBus(ABC):
    """Abstract event bus for DIKIWI coordination.

    The event bus enables:
    - Decoupled communication between stages
    - Audit trail (all events logged)
    - Async processing (handlers don't block)
    - Multiple subscribers per event type
    """

    @abstractmethod
    async def publish(self, event: Event) -> None:
        """Publish an event to all subscribers.

        Args:
            event: The event to publish
        """
        pass

    @abstractmethod
    def subscribe(
        self,
        event_type: type[T],
        handler: EventHandler,
    ) -> Callable[[], None]:
        """Subscribe to events of a specific type.

        Args:
            event_type: The event class to subscribe to
            handler: Async function to call when event occurs

        Returns:
            Unsubscribe function
        """
        pass

    @abstractmethod
    async def close(self) -> None:
        """Close the event bus and clean up resources."""
        pass


class InMemoryEventBus(EventBus):
    """In-memory event bus for development and testing.

    Uses asyncio for async handling.
    Not suitable for production (no persistence).
    """

    def __init__(self) -> None:
        self._subscribers: dict[type[Event], list[EventHandler]] = defaultdict(list)
        self._lock = asyncio.Lock()
        self._closed = False

    async def publish(self, event: Event) -> None:
        """Publish event to all subscribers asynchronously."""
        if self._closed:
            logger.warning("EventBus closed, dropping event: %s", event.event_type)
            return

        event_type = type(event)

        async with self._lock:
            # Collect handlers for the exact event type and all base classes
            handlers: list[EventHandler] = []
            seen = set()
            for cls in event_type.__mro__:
                for handler in self._subscribers.get(cls, []):
                    if id(handler) not in seen:
                        handlers.append(handler)
                        seen.add(id(handler))

        if not handlers:
            logger.debug("No handlers for event type: %s", event_type.__name__)
            return

        # Call all handlers concurrently
        logger.debug(
            "Publishing %s to %d handlers (correlation: %s)",
            event_type.__name__,
            len(handlers),
            event.correlation_id[:8] if event.correlation_id else "none",
        )

        tasks = [self._safe_call(handler, event) for handler in handlers]
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _safe_call(self, handler: EventHandler, event: Event) -> None:
        """Call handler with error isolation."""
        try:
            result = handler(event)
            if asyncio.iscoroutine(result):
                await result
        except Exception as e:
            logger.exception(
                "Event handler failed for %s: %s",
                type(event).__name__,
                e,
            )

    def subscribe(
        self,
        event_type: type[T],
        handler: EventHandler,
    ) -> Callable[[], None]:
        """Subscribe to events.

        Returns unsubscribe function.
        """
        self._subscribers[event_type].append(handler)
        logger.debug("Subscribed %s to %s", handler.__name__, event_type.__name__)

        def unsubscribe() -> None:
            if handler in self._subscribers[event_type]:
                self._subscribers[event_type].remove(handler)
                logger.debug("Unsubscribed %s from %s", handler.__name__, event_type.__name__)

        return unsubscribe

    def subscribe_all(self, handler: EventHandler) -> Callable[[], None]:
        """Subscribe to all event types.

        Useful for audit logging.
        """
        from aily.dikiwi.events.models import Event

        return self.subscribe(Event, handler)

    async def close(self) -> None:
        """Close the event bus."""
        self._closed = True
        async with self._lock:
            self._subscribers.clear()
        logger.info("EventBus closed")


class RedisStreamsEventBus(EventBus):
    """Redis Streams backend for production deployments.

    Provides:
    - Persistence (events survive process restarts)
    - Horizontal scaling (multiple consumers)
    - Consumer groups (load balancing)
    - Message acknowledgment

    Requires redis-py package.
    """

    def __init__(
        self,
        redis_url: str = REDIS_URL,
        stream_key: str = REDIS_STREAM_KEY,
        consumer_group: str = REDIS_CONSUMER_GROUP,
        consumer_name: str | None = None,
    ) -> None:
        # Fail fast if redis package not available
        try:
            import redis
        except ImportError as e:
            raise ImportError(
                "Redis support requires 'redis' package. "
                "Install with: pip install redis"
            ) from e

        self.redis_url = redis_url
        self.stream_key = stream_key
        self.consumer_group = consumer_group
        self.consumer_name = consumer_name or f"consumer-{id(self)}"
        self._subscribers: dict[type[Event], list[EventHandler]] = defaultdict(list)
        self._running = False
        self._listen_task: asyncio.Task | None = None
        self._redis: Any = None

    async def _get_redis(self) -> Any:
        """Get or create Redis connection."""
        if self._redis is None:
            try:
                import redis.asyncio as redis
                self._redis = await redis.from_url(
                    self.redis_url,
                    decode_responses=True,
                )
                # Ensure consumer group exists
                try:
                    await self._redis.xgroup_create(
                        self.stream_key,
                        self.consumer_group,
                        id="0",
                        mkstream=True,
                    )
                except Exception as e:
                    # Group may already exist
                    logger.debug("Consumer group may already exist: %s", e)
            except ImportError as e:
                raise ImportError(
                    "Redis support requires 'redis' package. "
                    "Install with: pip install redis"
                ) from e
        return self._redis

    async def publish(self, event: Event) -> None:
        """Publish event to Redis Stream."""
        redis = await self._get_redis()

        # Serialize event
        event_data = {
            "event_type": event.event_type,
            "correlation_id": event.correlation_id,
            "timestamp": event.timestamp.isoformat(),
            "payload": json.dumps(event.to_dict()),
        }

        try:
            await redis.xadd(self.stream_key, event_data)
            logger.debug(
                "Published %s to Redis Stream (correlation: %s)",
                event.event_type,
                event.correlation_id[:8] if event.correlation_id else "none",
            )
        except Exception as e:
            logger.exception("Failed to publish event to Redis: %s", e)
            raise

    def subscribe(
        self,
        event_type: type[T],
        handler: EventHandler,
    ) -> Callable[[], None]:
        """Subscribe to events."""
        self._subscribers[event_type].append(handler)
        logger.debug("Subscribed %s to %s", handler.__name__, event_type.__name__)

        # Start listening if not already running
        if not self._running:
            self._start_listening()

        def unsubscribe() -> None:
            if handler in self._subscribers[event_type]:
                self._subscribers[event_type].remove(handler)
                logger.debug("Unsubscribed %s from %s", handler.__name__, event_type.__name__)

        return unsubscribe

    def _start_listening(self) -> None:
        """Start background listener task."""
        if self._listen_task is None or self._listen_task.done():
            self._running = True
            self._listen_task = asyncio.create_task(self._listen_loop())
            logger.info("Started Redis Streams listener")

    async def _listen_loop(self) -> None:
        """Background loop to consume events from Redis."""
        while self._running:
            try:
                redis = await self._get_redis()

                # Read from consumer group
                messages = await redis.xreadgroup(
                    self.consumer_group,
                    self.consumer_name,
                    {self.stream_key: ">"},
                    count=10,
                    block=1000,
                )

                for stream_name, stream_messages in messages:
                    for msg_id, msg_data in stream_messages:
                        await self._process_message(msg_id, msg_data)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.exception("Error in Redis listener loop: %s", e)
                await asyncio.sleep(1)

    async def _process_message(self, msg_id: str, msg_data: dict) -> None:
        """Process a message from Redis."""
        try:
            from aily.dikiwi.events.models import Event

            event_type = msg_data.get("event_type")
            payload = json.loads(msg_data.get("payload", "{}"))

            # Reconstruct event (simplified - full implementation would
            # deserialize to specific event types)
            event = Event.from_dict(payload) if hasattr(Event, "from_dict") else None

            if event:
                # Call subscribers
                handlers = self._subscribers.get(type(event), [])
                for handler in handlers:
                    await self._safe_call(handler, event)

            # Acknowledge message
            redis = await self._get_redis()
            await redis.xack(self.stream_key, self.consumer_group, msg_id)

        except Exception as e:
            logger.exception("Failed to process message %s: %s", msg_id, e)

    async def _safe_call(self, handler: EventHandler, event: Event) -> None:
        """Call handler with error isolation."""
        try:
            result = handler(event)
            if asyncio.iscoroutine(result):
                await result
        except Exception as e:
            logger.exception(
                "Event handler failed for %s: %s",
                type(event).__name__,
                e,
            )

    async def close(self) -> None:
        """Close the event bus."""
        self._running = False

        if self._listen_task:
            self._listen_task.cancel()
            try:
                await self._listen_task
            except asyncio.CancelledError:
                pass

        if self._redis:
            await self._redis.close()

        logger.info("Redis Streams EventBus closed")


class EventBusDecorator:
    """Decorator for event handlers.

    Usage:
        @on_event(StageCompletedEvent)
        async def my_handler(event: StageCompletedEvent):
            ...
    """

    def __init__(self, bus: EventBus) -> None:
        self.bus = bus

    def __call__(
        self,
        event_type: type[T],
    ) -> Callable[[EventHandler], EventHandler]:
        """Create decorator for specific event type."""

        def decorator(handler: EventHandler) -> EventHandler:
            self.bus.subscribe(event_type, handler)
            return handler

        return decorator


def create_event_bus(use_redis: bool = False) -> EventBus:
    """Factory for creating event bus.

    Args:
        use_redis: If True, use Redis Streams backend

    Returns:
        Configured EventBus instance
    """
    if use_redis:
        try:
            return RedisStreamsEventBus()
        except Exception as e:
            logger.warning(
                "Failed to create Redis Streams backend: %s. "
                "Falling back to in-memory.",
                e,
            )

    return InMemoryEventBus()
