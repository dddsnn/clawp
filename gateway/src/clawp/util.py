# Copyright 2026 Marc Lehmann

# This file is part of clawp.
#
# clawp is free software: you can redistribute it and/or modify it under the
# terms of the GNU Affero General Public License as published by the Free
# Software Foundation, either version 3 of the License, or (at your option) any
# later version.
#
# clawp is distributed in the hope that it will be useful, but WITHOUT ANY
# WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR
# A PARTICULAR PURPOSE. See the GNU Affero General Public License for more
# details.
#
# You should have received a copy of the GNU Affero General Public License
# along with clawp. If not, see <https://www.gnu.org/licenses/>.

import abc
import asyncio
import collections.abc as cl_abc
import contextlib
import dataclasses as dc
import itertools as it
import sys
import typing as t


def create_done_future[ResultType](
    result: ResultType,
) -> asyncio.Future[ResultType]:
    """Create a future that is already done."""
    future = asyncio.get_running_loop().create_future()
    future.set_result(result)
    return future


class Missing:
    pass


MISSING = Missing()
"""Sentinel for missing values."""


class StreamableList[ElementType]:
    """
    A list that can be streamed asynchronously.

    __bool__, __getitem__, and __iter__ work on the underlying list.

    The stream() generator can be asynchronously iterated over, yielding
    elements as they are added via append(). The generator keeps waiting for
    new elements until finalize() is called.

    After finalize() is called, no more elements can be added. finalize() must
    be called eventually so that the task waiting for it can finish.

    The list can be initialized with content, in which case it is immediately
    finalized (i.e. no more elements can be added).
    """

    def __init__(self, content: list[ElementType] | None = None):
        self._new_element_condition = asyncio.Condition()
        self._num_readers = 0
        self._num_readers_condition = asyncio.Condition()
        self._finalized_event = asyncio.Event()
        self._finalized_wait_task = asyncio.create_task(
            self._finalized_event.wait()
        )
        if content is None:
            self._list = []
        else:
            self._list = content
            self._finalized_event.set()

    def __bool__(self) -> bool:
        return bool(self._list)

    def __getitem__(self, index) -> ElementType:
        return self._list[index]

    def __iter__(self) -> cl_abc.Iterator[ElementType]:
        return iter(self._list)

    async def append(self, item: ElementType) -> None:
        """
        Append an element.

        The list must not be finalized, or a ValueError is raised.
        """
        if self.finalized():
            raise ValueError("StreamableList has already been finalized")
        self._list.append(item)
        async with self._new_element_condition:
            self._new_element_condition.notify_all()

    async def finalize(
        self,
        compact: cl_abc.Callable[[list[ElementType]], list[ElementType]]
        | None = None,
    ) -> None:
        """
        Finalize the list.

        This puts the stream into a read-only state (any appends() will now
        raise exceptions), and stops the iteration of any asynchronous streams
        (via stream()).

        :param compact: An optional function to make the list more compact
            (e.g. by concatenating strings). This will be given the underlying
            list and must return the compacted list.
        """
        self._finalized_event.set()
        if compact:
            async with self._num_readers_condition:
                await self._num_readers_condition.wait_for(
                    lambda: self._num_readers == 0
                )
                self._list = compact(self._list)

    async def wait_finalized(self) -> None:
        """
        Wait until the list has been finalized.

        When the list is finalized, no new elements can be added.
        """
        await asyncio.shield(self._finalized_wait_task)

    def finalized(self) -> bool:
        """Check whether the list is already finalized."""
        return self._finalized_event.is_set()

    async def stream(self) -> cl_abc.AsyncGenerator[ElementType]:
        """
        Asynchronously stream list elements.

        Existing elements are yielded, as well as new ones added via append().
        Once the list is finalized and no more elements can be added, the
        generator exits.
        """
        try:
            self._num_readers += 1
            i = 0
            while True:
                if i < len(self._list):
                    yield self._list[i]
                    i += 1
                    continue
                elif self.finalized():
                    return
                new_element_wait_task = asyncio.create_task(
                    self._wait_for_new_element()
                )
                await asyncio.wait(
                    {new_element_wait_task, self._finalized_wait_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                new_element_wait_task.cancel()
        finally:
            self._num_readers -= 1
            assert self._num_readers >= 0
            async with self._num_readers_condition:
                self._num_readers_condition.notify_all()

    async def _wait_for_new_element(self):
        async with self._new_element_condition:
            await self._new_element_condition.wait()


class PublisherNotRunningError(Exception):
    """Raised when a Publisher is not running."""


class Publisher[ElementType]:
    """
    A publisher of elements.

    Elements can be appended. Clients can subscribe to a stream of elements.
    When the asynchronous context manager exits, the streams of all subscribers
    exit.
    """

    @dc.dataclass
    class SeqElement:
        seq: int
        element: ElementType

    def __init__(self):
        self._condition = asyncio.Condition()
        self._running = False

        self._history = []
        self._next_seq = 0

        self._subscriber_id_gen = it.count()
        self._subscriber_next_seq = {}

    async def __aenter__(self) -> t.Self:
        self._running = True
        return self

    async def __aexit__(self, *_) -> bool:
        async with self._condition:
            self._running = False
            self._condition.notify_all()
        return False

    async def append(self, element: ElementType) -> None:
        """
        Append a new element.

        All current subscribers will receive the new element.
        """
        async with self._condition:
            if not self._running:
                raise PublisherNotRunningError
            self._history.append(self.SeqElement(self._next_seq, element))
            self._next_seq += 1
            self._condition.notify_all()

    async def subscribe(self) -> cl_abc.AsyncGenerator[ElementType]:
        """
        Subscribe to the elements of this publisher.

        Asynchronously iterates over elements, yielding new ones as they are
        published. The first element yielded is the first one that is appended
        after the subscription starts.

        The generator exits when the publisher shuts down.
        """
        if not self._running:
            raise PublisherNotRunningError
        subscriber_id = next(self._subscriber_id_gen)
        self._subscriber_next_seq[subscriber_id] = self._next_seq
        try:
            while True:
                wanted_seq = self._subscriber_next_seq[subscriber_id]
                try:
                    element = next(
                        se.element
                        for se in self._history
                        if se.seq == wanted_seq
                    )
                    yield element
                    self._subscriber_next_seq[subscriber_id] += 1
                    self._prune_history()
                except StopIteration:
                    async with self._condition:

                        def have_data(wanted_seq=wanted_seq):
                            return (
                                not self._running
                                or wanted_seq < self._next_seq
                            )

                        await self._condition.wait_for(have_data)
                if not self._running:
                    return
        finally:
            del self._subscriber_next_seq[subscriber_id]
            self._prune_history()

    def _prune_history(self) -> None:
        min_next_seq = min(
            self._subscriber_next_seq.values(), default=float("inf")
        )
        prune_count = sum(1 for se in self._history if se.seq < min_next_seq)
        del self._history[:prune_count]


class Value[ValueType](abc.ABC):
    """A value wrapper."""

    @property
    @abc.abstractmethod
    async def value(self) -> ValueType:
        raise NotImplementedError


class ImmediateValue[ValueType](Value[ValueType]):
    """A value that is immediately available."""

    def __init__(self, value):
        self._value = value

    @property
    async def value(self) -> ValueType:
        return self._value


class FutureValue[ValueType](Value[ValueType]):
    """A value that will be available in the future."""

    def __init__(self):
        self._set_event = asyncio.Event()
        self._value = None

    @property
    async def value(self) -> ValueType:
        await self._set_event.wait()
        assert self._value is not None
        return self._value

    @value.setter
    def value(self, value) -> None:
        if self._set_event.is_set():
            raise ValueError("value has already been set")
        self._value = value
        self._set_event.set()


class TtlCache[ValueType]:
    """
    Simple TTL cache for an asynchronous refresh function.

    The first call to get(), and the first call after the TTL expires, refresh
    the value. Concurrent callers share that refresh. Failed or cancelled
    refreshes are not cached.
    """

    def __init__(
        self,
        ttl: float,
        refresh: cl_abc.Callable[
            [], cl_abc.Coroutine[t.Any, t.Any, ValueType]
        ],
    ) -> None:
        """
        :param ttl: Time in seconds for which the value is cached (using the
            event loop's monotonic clock).
        """
        self._ttl = ttl
        self._refresh = refresh
        self._lock = asyncio.Lock()
        self._value: ValueType | None = None
        self._expires_at = float("-inf")

    async def get(self) -> ValueType:
        """Return the cached value, refreshing it when it has expired."""
        if self._is_fresh():
            assert self._value is not None
            return self._value
        async with self._lock:
            if self._is_fresh():
                assert self._value is not None
                return self._value
            self._value = await self._refresh()
            self._expires_at = asyncio.get_running_loop().time() + self._ttl
            return self._value

    def _is_fresh(self) -> bool:
        return asyncio.get_running_loop().time() < self._expires_at


type CM[EnterReturn] = contextlib.AbstractAsyncContextManager[
    EnterReturn, t.Any
]


class DependencyContextManager[Primary]:
    """
    A context manager wrapping a primary context manager and dependencies.

    Multiple context managers can be registered via register_primary() and
    register_dependency(), although register_primary() must be called exactly
    once. Context managers can be specified as the object itself, or a
    factory that returns an awaitable that returns the context manager.

    On __aenter__(), all registered context managers are entered and the
    primary context manager returned. Any context managers specified as a
    factory are created just before they are entered. On __aexit__(), the
    context managers are exited in reverse order, respecting exception
    handling.

    Registrations must be made before __aenter__() is called. This context
    manager is single-use: after __aenter__() has been called, it cannot be
    entered or registered with again.
    """

    def __init__(self) -> None:
        self._registrations = []
        self._exit_stack = contextlib.AsyncExitStack()
        self._entered = False

    async def __aenter__(self) -> Primary:
        if self._entered:
            raise RuntimeError("DependencyContextManager is single-use")
        if not self._has_primary():
            raise ValueError("a primary context manager must be registered")
        self._entered = True
        primary_value = MISSING

        try:
            for is_primary, registration in self._registrations:
                context_manager = await self._resolve_context_manager(
                    registration
                )
                value = await self._exit_stack.enter_async_context(
                    context_manager
                )
                if is_primary:
                    primary_value = value
        except BaseException:
            await self._exit_stack.__aexit__(*sys.exc_info())
            raise

        assert not isinstance(primary_value, Missing)
        return primary_value

    async def __aexit__(self, exc_type, exc, tb) -> bool | None:
        return await self._exit_stack.__aexit__(exc_type, exc, tb)

    def _has_primary(self) -> bool:
        return any(is_primary for is_primary, _ in self._registrations)

    def register_primary(
        self,
        cm: CM[Primary] | cl_abc.Callable[[], cl_abc.Awaitable[CM[Primary]]],
    ) -> None:
        if self._has_primary():
            raise ValueError("a primary context manager is already registered")
        self._register(cm, True)

    def register_dependency(
        self, cm: CM[t.Any] | cl_abc.Callable[[], cl_abc.Awaitable[CM[t.Any]]]
    ) -> None:
        self._register(cm, False)

    def _register(
        self,
        cm: CM[Primary] | cl_abc.Callable[[], cl_abc.Awaitable[CM[Primary]]],
        is_primary: bool,
    ) -> None:
        if self._entered:
            raise RuntimeError("DependencyContextManager is already entered")
        self._registrations.append((is_primary, cm))

    async def _resolve_context_manager(
        self,
        registration: CM[t.Any]
        | cl_abc.Callable[[], cl_abc.Awaitable[CM[t.Any]]],
    ) -> CM[t.Any]:
        if isinstance(registration, contextlib.AbstractAsyncContextManager):
            return registration
        return await registration()
