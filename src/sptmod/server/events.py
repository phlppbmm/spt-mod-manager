"""In-process progress broker and the SSE endpoint that streams it."""

import asyncio
import threading
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from typing import Final
from uuid import uuid4

from fastapi import APIRouter
from sse_starlette.event import ServerSentEvent
from sse_starlette.sse import EventSourceResponse

from sptmod.core.models import Progress
from sptmod.server.schemas import ProgressEvent

type _Subscriber = tuple[asyncio.AbstractEventLoop, asyncio.Queue[ProgressEvent]]


class ProgressBroker:
    """Fan-out of progress events from sync workers to async subscribers.

    Service functions run in the sync threadpool, so :meth:`publish` must be
    callable from threads. Events are queued to each subscriber's event loop
    using :func:`asyncio.BaseEventLoop.call_soon_threadsafe` for safety.
    """

    def __init__(self) -> None:
        self._subscribers: list[_Subscriber] = []
        self._lock = threading.Lock()

    def publish(self, event: ProgressEvent) -> None:
        """Deliver ``event`` to every current subscriber."""
        with self._lock:
            subscribers = list(self._subscribers)
        for loop, queue in subscribers:
            loop.call_soon_threadsafe(queue.put_nowait, event)

    @contextmanager
    def subscribe(self) -> Iterator[asyncio.Queue[ProgressEvent]]:
        """Yield a queue receiving every event published while it is open."""
        subscriber: _Subscriber = (asyncio.get_running_loop(), asyncio.Queue())
        with self._lock:
            self._subscribers.append(subscriber)
        try:
            yield subscriber[1]
        finally:
            with self._lock:
                self._subscribers.remove(subscriber)


broker: Final = ProgressBroker()

router: Final = APIRouter()


def new_operation_id() -> str:
    """Return a fresh ID naming the progress stream of one operation."""
    return uuid4().hex


def progress_callback(operation_id: str) -> Progress:
    """Return a core progress callback publishing under ``operation_id``."""

    def report(step: str, percent: int, message: str) -> None:
        broker.publish(
            ProgressEvent(
                operation_id=operation_id,
                step=step,
                percent=percent,
                message=message,
            )
        )

    return report


class ProgressEventStream(EventSourceResponse):
    """Event stream whose every message carries one JSON ``ProgressEvent``."""

    media_type = "text/event-stream"


@router.get(
    "/events",
    response_model=ProgressEvent,
    response_class=ProgressEventStream,
    summary="Stream the progress of long-running operations",
)
async def events() -> ProgressEventStream:
    """Stream one message per progress callback of any running operation."""

    async def stream() -> AsyncIterator[ServerSentEvent]:
        with broker.subscribe() as queue:
            while True:
                event = await queue.get()
                yield ServerSentEvent(data=event.model_dump_json())

    return ProgressEventStream(stream())
