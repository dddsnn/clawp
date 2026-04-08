import asyncio

import pytest

import util


class TestStreamableList:
    @pytest.fixture
    async def ls(self):
        return util.StreamableList()

    @pytest.fixture
    def stream_into_list(self, ls):
        async def streamer(output):
            async for element in ls.stream():
                output.append(element)
            output.append("finalized")

        return streamer

    async def wait_for_list_content(self, output, expected):
        while output != expected:
            await asyncio.sleep(10**-3)

    @pytest.fixture
    def read_then_wait(self, ls):
        async def waiter(continue_condition, element_queue):
            async for element in ls.stream():
                async with continue_condition:
                    await element_queue.put(element)
                    await continue_condition.wait()

        return waiter

    async def test_is_empty_on_construction(self, ls):
        assert not ls
        assert list(ls) == []
        with pytest.raises(IndexError):
            ls[0]

    async def test_append(self, ls):
        await ls.append(1)
        assert ls
        assert list(ls) == [1]
        assert ls[0] == 1

    async def test_append_raises_if_finalized(self, ls):
        await ls.finalize()
        with pytest.raises(ValueError):
            await ls.append(1)

    async def test_wait_finalize_waits_until_finalized(self, ls):
        wait_task = asyncio.create_task(ls.wait_finalized())
        with pytest.raises(asyncio.TimeoutError):
            async with asyncio.timeout(10**-3):
                await asyncio.shield(wait_task)
        await ls.finalize()
        async with asyncio.timeout(10**-3):
            await wait_task

    async def test_stream(self, ls, stream_into_list):
        output = []
        stream_task = asyncio.create_task(stream_into_list(output))
        await ls.append(1)
        await self.wait_for_list_content(output, [1])
        await ls.append(2)
        await self.wait_for_list_content(output, [1, 2])
        await ls.finalize()
        await stream_task

    async def test_stream_blocks_while_not_finalized(
            self, ls, stream_into_list):
        stream_task = asyncio.create_task(stream_into_list([]))
        with pytest.raises(TimeoutError):
            async with asyncio.timeout(10**-3):
                await asyncio.shield(stream_task)
        await ls.finalize()
        await stream_task

    async def test_stream_exits_when_finalized(self, ls, stream_into_list):
        output = []
        stream_task = asyncio.create_task(stream_into_list(output))
        await ls.finalize()
        await self.wait_for_list_content(output, ["finalized"])
        await stream_task

    async def test_compact_on_finalize(self, ls):
        await ls.append(1)
        await ls.append(2)
        await ls.finalize(compact=lambda ls: [sum(ls)])
        assert list(ls) == [3]

    async def test_compact_waits_until_readers_done(self, ls, read_then_wait):
        await ls.append(1)
        continue_condition = asyncio.Condition()
        read_task = asyncio.create_task(
            read_then_wait(continue_condition, asyncio.Queue()))
        finalize_task = asyncio.create_task(
            ls.finalize(compact=lambda ls: [sum(ls) + 0.5]))
        with pytest.raises(TimeoutError):
            async with asyncio.timeout(10**-3):
                await asyncio.shield(finalize_task)
        assert list(ls) == [1]
        async with continue_condition:
            continue_condition.notify_all()
        await finalize_task
        await read_task
        assert list(ls) == [1.5]

    async def test_compact_waits_if_new_reader_is_added_later(
            self, ls, read_then_wait):
        await ls.append(1)
        await ls.append(2)
        continue_condition = asyncio.Condition()
        queue_1 = asyncio.Queue()
        read_task_1 = asyncio.create_task(
            read_then_wait(continue_condition, queue_1))
        await queue_1.get()
        async with continue_condition:
            continue_condition.notify_all()
        finalize_task = asyncio.create_task(
            ls.finalize(compact=lambda ls: [sum(ls) + 0.5]))
        queue_2 = asyncio.Queue()
        read_task_2 = asyncio.create_task(
            read_then_wait(continue_condition, queue_2))
        await queue_1.get()
        await queue_2.get()
        async with continue_condition:
            continue_condition.notify_all()
        await read_task_1
        with pytest.raises(TimeoutError):
            async with asyncio.timeout(10**-3):
                await asyncio.shield(finalize_task)
        async with continue_condition:
            continue_condition.notify_all()
        await queue_2.get()
        await read_task_2
        await finalize_task
        assert list(ls) == [3.5]
