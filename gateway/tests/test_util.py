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
import contextlib

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


class TestPublisher:
    async def wait_for_list_content(self, output, expected, timeout=0.2):
        async with asyncio.timeout(timeout):
            while output != expected:
                await asyncio.sleep(10**-3)

    async def stream_into_list(self, publisher, output, stream_start_event):
        async for element in self.aiter_with_start_event(publisher.subscribe(),
                                                         stream_start_event):
            output.append(element)
        output.append("end")

    async def aiter_with_start_event(self, aiter, stream_start_event):
        get_task = None
        while True:
            get_task = get_task or asyncio.create_task(anext(aiter))
            try:
                done, _ = await asyncio.wait({get_task}, timeout=10**-3)
                stream_start_event.set()
                if done:
                    yield get_task.result()
                    get_task = None
            except StopAsyncIteration:
                break

    async def stream_with_manual_continue(
            self, publisher, output_queue, stream_start_event,
            continue_condition):
        async for element in self.aiter_with_start_event(publisher.subscribe(),
                                                         stream_start_event):
            async with continue_condition:
                await output_queue.put(element)
                await continue_condition.wait()
        await output_queue.put("end")

    async def wait_until(self, predicate):
        while not predicate():
            await asyncio.sleep(10**-3)

    async def test_append_raises_if_not_running(self):
        publisher = util.Publisher()
        with pytest.raises(ValueError):
            await publisher.append(1)
        async with publisher:
            pass
        with pytest.raises(ValueError):
            await publisher.append(1)

    async def test_subscription_raises_if_not_running(self):
        publisher = util.Publisher()
        with pytest.raises(ValueError):
            await self.stream_into_list(publisher, [], asyncio.Event())
        async with publisher:
            pass
        with pytest.raises(ValueError):
            await self.stream_into_list(publisher, [], asyncio.Event())

    async def test_subscription_yields_new_elements(self):
        output = []
        stream_start_event = asyncio.Event()
        async with util.Publisher() as publisher:
            subscription_task = asyncio.create_task(
                self.stream_into_list(publisher, output, stream_start_event))
            await stream_start_event.wait()
            await publisher.append(1)
            await self.wait_for_list_content(output, [1])
            await publisher.append(2)
            await self.wait_for_list_content(output, [1, 2])

        await subscription_task

    async def test_subscription_doesnt_include_old_elements(self):
        output = []
        stream_start_event = asyncio.Event()
        async with util.Publisher() as publisher:
            await publisher.append("old")
            subscription_task = asyncio.create_task(
                self.stream_into_list(publisher, output, stream_start_event))
            await stream_start_event.wait()
            await publisher.append("new")
            await self.wait_for_list_content(output, ["new"])

        await subscription_task
        assert output == ["new", "end"]

    async def test_multiple_subscribers(self):
        output_1, output_2 = [], []
        stream_start_event_1 = asyncio.Event()
        stream_start_event_2 = asyncio.Event()
        async with util.Publisher() as publisher:
            subscription_task_1 = asyncio.create_task(
                self.stream_into_list(
                    publisher, output_1, stream_start_event_1))
            await stream_start_event_1.wait()
            await publisher.append("a")
            subscription_task_2 = asyncio.create_task(
                self.stream_into_list(
                    publisher, output_2, stream_start_event_2))
            await stream_start_event_2.wait()
            await publisher.append("b")
            await self.wait_for_list_content(output_1, ["a", "b"])
            await self.wait_for_list_content(output_2, ["b"])

        await subscription_task_1
        await subscription_task_2
        assert output_1 == ["a", "b", "end"]
        assert output_2 == ["b", "end"]

    async def test_retains_history_of_elements_for_slow_subscribers(self):
        fast_output = []
        continue_condition = asyncio.Condition()
        slow_element_queue = asyncio.Queue()
        fast_start_event = asyncio.Event()
        slow_start_event = asyncio.Event()

        async with util.Publisher() as publisher:
            slow_task = asyncio.create_task(
                self.stream_with_manual_continue(
                    publisher, slow_element_queue, slow_start_event,
                    continue_condition))
            fast_task = asyncio.create_task(
                self.stream_into_list(
                    publisher, fast_output, fast_start_event))
            await fast_start_event.wait()
            await slow_start_event.wait()
            await publisher.append(1)
            assert await slow_element_queue.get() == 1

            await publisher.append(2)
            await self.wait_for_list_content(fast_output, [1, 2])

            with pytest.raises(TimeoutError):
                async with asyncio.timeout(10**-3):
                    await slow_element_queue.get()

            async with continue_condition:
                continue_condition.notify_all()
            assert await slow_element_queue.get() == 2

        async with continue_condition:
            continue_condition.notify_all()
        assert await slow_element_queue.get() == "end"

        await slow_task
        await fast_task
        assert fast_output == [1, 2, "end"]

    async def test_prunes_unneded_element_history(self):
        continue_condition = asyncio.Condition()
        element_queue = asyncio.Queue()
        stream_start_event = asyncio.Event()

        async with util.Publisher() as publisher:
            read_task = asyncio.create_task(
                self.stream_with_manual_continue(
                    publisher, element_queue, stream_start_event,
                    continue_condition))
            await stream_start_event.wait()
            await publisher.append(1)
            await publisher.append(2)
            await publisher.append(3)
            assert await element_queue.get() == 1
            async with continue_condition:
                continue_condition.notify_all()
            await self.wait_until(lambda: len(publisher._history) == 2)

            assert await element_queue.get() == 2
            async with continue_condition:
                continue_condition.notify_all()
            await self.wait_until(lambda: len(publisher._history) == 1)

            assert await element_queue.get() == 3
            async with continue_condition:
                continue_condition.notify_all()
            await self.wait_until(lambda: len(publisher._history) == 1)

        await read_task

    async def test_cleans_up_on_subscriber_exit(self):
        element_queue = asyncio.Queue()
        stream_start_event = asyncio.Event()

        async with util.Publisher() as publisher:
            read_task = asyncio.create_task(
                self.stream_with_manual_continue(
                    publisher, element_queue, stream_start_event,
                    asyncio.Condition()))
            await stream_start_event.wait()
            await publisher.append(1)
            await publisher.append(2)
            await self.wait_until(lambda: len(publisher._history) == 2)
            await self.wait_until(
                lambda: len(publisher._subscriber_next_seq) == 1)
            read_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await read_task
            await self.wait_until(lambda: len(publisher._history) == 0)
            await self.wait_until(
                lambda: len(publisher._subscriber_next_seq) == 0)

    async def test_subscriptions_exit_on_aexit(self):
        output = []
        stream_start_event = asyncio.Event()
        async with util.Publisher() as publisher:
            subscription_task = asyncio.create_task(
                self.stream_into_list(publisher, output, stream_start_event))
            await stream_start_event.wait()
            with pytest.raises(TimeoutError):
                async with asyncio.timeout(10**-3):
                    await asyncio.shield(subscription_task)

        await subscription_task
        assert output == ["end"]
