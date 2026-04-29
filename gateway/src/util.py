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
# You should have received a copy of the GNU Affero General Public License along
# with clawp. If not, see <https://www.gnu.org/licenses/>.

import asyncio
import collections as cl
import collections.abc as cl_abc
import itertools as it


class StreamableList:
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
    def __init__(self, content: list | None = None):
        self._new_element_condition = asyncio.Condition()
        self._num_readers = 0
        self._num_readers_condition = asyncio.Condition()
        self._finalized_event = asyncio.Event()
        self._finalized_wait_task = asyncio.create_task(
            self._finalized_event.wait())
        if content is None:
            self._list = []
        else:
            self._list = content
            self._finalized_event.set()

    def __bool__(self) -> bool:
        return bool(self._list)

    def __getitem__(self, index):
        return self._list[index]

    def __iter__(self) -> cl_abc.Iterator:
        return iter(self._list)

    async def append(self, item) -> None:
        """
        Append an element.

        The list must not be finalized, or a ValueError is raised.
        """
        if self._finalized_event.is_set():
            raise ValueError("StreamableList has already been finalized")
        self._list.append(item)
        async with self._new_element_condition:
            self._new_element_condition.notify_all()

    async def finalize(self, compact=None) -> None:
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
                    lambda: self._num_readers == 0)
                self._list = compact(self._list)

    async def wait_finalized(self) -> None:
        """
        Wait until the list has been finalized.

        When the list is finalized, no new elements can be added.
        """
        await self._finalized_wait_task

    async def stream(self) -> cl_abc.AsyncGenerator:
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
                elif self._finalized_event.is_set():
                    return
                new_element_wait_task = asyncio.create_task(
                    self._wait_for_new_element())
                await asyncio.wait(
                    {new_element_wait_task, self._finalized_wait_task},
                    return_when=asyncio.FIRST_COMPLETED)
                new_element_wait_task.cancel()
        finally:
            self._num_readers -= 1
            assert self._num_readers >= 0
            async with self._num_readers_condition:
                self._num_readers_condition.notify_all()

    async def _wait_for_new_element(self):
        async with self._new_element_condition:
            await self._new_element_condition.wait()


class Publisher:
    """
    A publisher of elements.

    Elements can be appended. Clients can subscribe to a stream of elements.
    When the asynchronous context manager exits, the streams of all subscribers
    exit.
    """
    SeqElement = cl.namedtuple("SeqElement", ["seq", "element"])

    def __init__(self):
        self._condition = asyncio.Condition()
        self._running = False

        self._history = []
        self._next_seq = 0

        self._subscriber_id_gen = it.count()
        self._subscriber_next_seq = {}

    async def __aenter__(self) -> "Publisher":
        self._running = True
        return self

    async def __aexit__(self, *_) -> bool:
        async with self._condition:
            self._running = False
            self._condition.notify_all()
        return False

    async def append(self, element) -> None:
        """
        Append a new element.

        All current subscribers will receive the new element.
        """
        async with self._condition:
            if not self._running:
                raise ValueError("Publisher is not running")
            self._history.append(self.SeqElement(self._next_seq, element))
            self._next_seq += 1
            self._condition.notify_all()

    async def subscribe(self) -> cl_abc.AsyncGenerator:
        """
        Subscribe to the elements of this publisher.

        Asynchronously iterates over elements, yielding new ones as they are
        published. The first element yielded is the first one that is appended
        after the subscription starts.

        The generator exits when the publisher shuts down.
        """
        if not self._running:
            raise ValueError("Publisher is not running")
        subscriber_id = next(self._subscriber_id_gen)
        self._subscriber_next_seq[subscriber_id] = self._next_seq
        try:
            while True:
                wanted_seq = self._subscriber_next_seq[subscriber_id]
                try:
                    element = next(
                        se.element
                        for se in self._history
                        if se.seq == wanted_seq)
                    yield element
                    self._subscriber_next_seq[subscriber_id] += 1
                    self._prune_history()
                except StopIteration:
                    async with self._condition:

                        def have_data():
                            return (
                                not self._running
                                or wanted_seq < self._next_seq)

                        await self._condition.wait_for(have_data)
                if not self._running:
                    return
        except asyncio.CancelledError:
            return
        finally:
            del self._subscriber_next_seq[subscriber_id]
            self._prune_history()

    def _prune_history(self) -> None:
        min_next_seq = min(
            self._subscriber_next_seq.values(), default=float("inf"))
        prune_count = sum(1 for se in self._history if se.seq < min_next_seq)
        del self._history[:prune_count]
