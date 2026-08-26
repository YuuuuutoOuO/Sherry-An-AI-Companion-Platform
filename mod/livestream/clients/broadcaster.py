"""
mod/livestream/clients/broadcaster.py
=====================================
Concrete in-memory broadcaster managing Server-Sent Events (SSE) subscriber queues.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Set

from mod.livestream.contracts import ILivestreamBroadcaster
from mod.livestream.domain import StreamEvent, StreamPlaybackState

logger = logging.getLogger(__name__)


class LivestreamBroadcaster(ILivestreamBroadcaster):
    """In-memory event broadcaster for livestream UI clients."""

    def __init__(self) -> None:
        """Initializes the broadcaster with an empty subscriber set and default state."""
        self._subscribers: Set[asyncio.Queue[StreamEvent]] = set()
        self._state: StreamPlaybackState = StreamPlaybackState()
        self._lock: asyncio.Lock = asyncio.Lock()

    async def broadcast(self, event: StreamEvent) -> None:
        """Broadcasts a stream event to all active subscriber queues.

        Args:
            event: The stream event to broadcast.
        """
        async with self._lock:
            self._state = event.state
            dead_queues: Set[asyncio.Queue[StreamEvent]] = set()

            for q in self._subscribers:
                try:
                    q.put_nowait(event)
                except asyncio.QueueFull:
                    # Drop oldest if full and push new event
                    try:
                        _ = q.get_nowait()
                        q.put_nowait(event)
                    except Exception:
                        dead_queues.add(q)
                except Exception as exc:
                    logger.warning("Failed to dispatch stream event to a subscriber: %s", exc)
                    dead_queues.add(q)

            if dead_queues:
                self._subscribers.difference_update(dead_queues)
                logger.debug("Pruned %d disconnected livestream subscriber(s).", len(dead_queues))

    async def subscribe(self) -> asyncio.Queue[StreamEvent]:
        """Subscribes a new Web UI client queue for receiving real-time SSE stream events.

        Returns:
            An asyncio.Queue yielding StreamEvent instances.
        """
        queue: asyncio.Queue[StreamEvent] = asyncio.Queue(maxsize=100)
        async with self._lock:
            self._subscribers.add(queue)
            logger.debug(
                "New livestream client subscribed. Active listeners: %d",
                len(self._subscribers),
            )
        return queue

    async def unsubscribe(self, queue: asyncio.Queue[StreamEvent]) -> None:
        """Unsubscribes a client queue when disconnected.

        Args:
            queue: The subscriber queue to remove.
        """
        async with self._lock:
            self._subscribers.discard(queue)
            logger.debug(
                "Livestream client unsubscribed. Active listeners: %d",
                len(self._subscribers),
            )

    def get_state(self) -> StreamPlaybackState:
        """Retrieves the current cached playback state.

        Returns:
            The current StreamPlaybackState.
        """
        return self._state

    def set_state(self, state: StreamPlaybackState) -> None:
        """Updates the current cached playback state.

        Args:
            state: The new StreamPlaybackState.
        """
        self._state = state
