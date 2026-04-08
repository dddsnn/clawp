import asyncio
import collections.abc as cl_abc


class StreamableList:
    """
    A list that can be streamed asynchronously.

    __bool__, __getitem__, and __iter__ work on the underlying list.

    The stream() generator can be asynchronously iterated over, yielding
    elements as they are added via append(). The generator keeps waiting for
    new elements until finalize() is called.

    After finalize() is called, no more elements can be added. finalize() must
    be called eventually so that the task waiting for it can finish.
    """
    def __init__(self):
        self._list = []
        self._new_element_condition = asyncio.Condition()
        self._num_readers = 0
        self._num_readers_condition = asyncio.Condition()
        self._finalized_event = asyncio.Event()
        self._finalized_wait_task = asyncio.create_task(
            self._finalized_event.wait())

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
