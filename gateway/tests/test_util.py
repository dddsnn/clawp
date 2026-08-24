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

import asyncio
import contextlib
import itertools as it
import unittest.mock as um

import async_solipsism
import pytest

from clawp import util


class TestStreamableList:
    @pytest.fixture
    async def ls(self):
        return util.StreamableList()

    @pytest.fixture
    def stream_into_list(self, ls: util.StreamableList[float]):
        async def streamer(output):
            async for element in ls.stream():
                output.append(element)
            output.append("finalized")

        return streamer

    async def wait_for_list_content(self, output, expected):
        while output != expected:
            await asyncio.sleep(10**-3)

    @pytest.fixture
    def read_then_wait(self, ls: util.StreamableList[float]):
        async def waiter(continue_condition, element_queue):
            async for element in ls.stream():
                async with continue_condition:
                    await element_queue.put(element)
                    await continue_condition.wait()

        return waiter

    async def test_is_empty_on_construction(
        self, ls: util.StreamableList[float]
    ):
        assert not ls
        assert list(ls) == []
        with pytest.raises(IndexError):
            ls[0]

    async def test_append(self, ls: util.StreamableList[float]):
        await ls.append(1)
        assert ls
        assert list(ls) == [1]
        assert ls[0] == 1

    async def test_finalized(self, ls: util.StreamableList[float]):
        assert not ls.finalized()
        await ls.finalize()
        assert ls.finalized()

    async def test_append_raises_if_finalized(
        self, ls: util.StreamableList[float]
    ):
        await ls.finalize()
        with pytest.raises(ValueError):
            await ls.append(1)

    async def test_wait_finalize_waits_until_finalized(
        self, ls: util.StreamableList[float]
    ):
        wait_task = asyncio.create_task(ls.wait_finalized())
        with pytest.raises(asyncio.TimeoutError):
            async with asyncio.timeout(10**-3):
                await asyncio.shield(wait_task)
        await ls.finalize()
        async with asyncio.timeout(10**-3):
            await wait_task

    async def test_immediately_finalized_if_initialized_with_content(self):
        ls = util.StreamableList([1, 2, 3])
        with pytest.raises(ValueError):
            await ls.append(1)
        await ls.wait_finalized()

    async def test_timeout_in_wait_finalize_doesnt_cancel_other_waiters(
        self, ls: util.StreamableList[float]
    ):
        wait_task_1 = asyncio.create_task(ls.wait_finalized())
        wait_task_2 = asyncio.create_task(ls.wait_finalized())
        with pytest.raises(asyncio.TimeoutError):
            async with asyncio.timeout(0):
                await wait_task_1
        wait_task_1.cancel()
        await ls.finalize()
        await wait_task_2

    async def test_stream(
        self, ls: util.StreamableList[float], stream_into_list
    ):
        output = []
        stream_task = asyncio.create_task(stream_into_list(output))
        await ls.append(1)
        await self.wait_for_list_content(output, [1])
        await ls.append(2)
        await self.wait_for_list_content(output, [1, 2])
        await ls.finalize()
        await stream_task

    async def test_stream_blocks_while_not_finalized(
        self, ls: util.StreamableList[float], stream_into_list
    ):
        stream_task = asyncio.create_task(stream_into_list([]))
        with pytest.raises(TimeoutError):
            async with asyncio.timeout(10**-3):
                await asyncio.shield(stream_task)
        await ls.finalize()
        await stream_task

    async def test_stream_exits_when_finalized(
        self, ls: util.StreamableList[float], stream_into_list
    ):
        output = []
        stream_task = asyncio.create_task(stream_into_list(output))
        await ls.finalize()
        await self.wait_for_list_content(output, ["finalized"])
        await stream_task

    async def test_compact_on_finalize(self, ls: util.StreamableList[float]):
        await ls.append(1)
        await ls.append(2)
        await ls.finalize(compact=lambda ll: [sum(ll)])
        assert list(ls) == [3]

    async def test_compact_waits_until_readers_done(
        self, ls: util.StreamableList[float], read_then_wait
    ):
        await ls.append(1)
        continue_condition = asyncio.Condition()
        read_task = asyncio.create_task(
            read_then_wait(continue_condition, asyncio.Queue())
        )
        finalize_task = asyncio.create_task(
            ls.finalize(compact=lambda ll: [sum(ll) + 0.5])
        )
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
        self, ls: util.StreamableList[float], read_then_wait
    ):
        await ls.append(1)
        await ls.append(2)
        continue_condition = asyncio.Condition()
        queue_1 = asyncio.Queue()
        read_task_1 = asyncio.create_task(
            read_then_wait(continue_condition, queue_1)
        )
        await queue_1.get()
        async with continue_condition:
            continue_condition.notify_all()
        finalize_task = asyncio.create_task(
            ls.finalize(compact=lambda ll: [sum(ll) + 0.5])
        )
        queue_2 = asyncio.Queue()
        read_task_2 = asyncio.create_task(
            read_then_wait(continue_condition, queue_2)
        )
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
        async for element in self.aiter_with_start_event(
            publisher.subscribe(), stream_start_event
        ):
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
        self, publisher, output_queue, stream_start_event, continue_condition
    ):
        async for element in self.aiter_with_start_event(
            publisher.subscribe(), stream_start_event
        ):
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
        subscription_task = None
        async with util.Publisher() as publisher:
            subscription_task = asyncio.create_task(
                self.stream_into_list(publisher, output, stream_start_event)
            )
            await stream_start_event.wait()
            await publisher.append(1)
            await self.wait_for_list_content(output, [1])
            await publisher.append(2)
            await self.wait_for_list_content(output, [1, 2])
        assert subscription_task
        await subscription_task

    async def test_subscription_doesnt_include_old_elements(self):
        output = []
        stream_start_event = asyncio.Event()
        subscription_task = None
        async with util.Publisher() as publisher:
            await publisher.append("old")
            subscription_task = asyncio.create_task(
                self.stream_into_list(publisher, output, stream_start_event)
            )
            await stream_start_event.wait()
            await publisher.append("new")
            await self.wait_for_list_content(output, ["new"])
        assert subscription_task
        await subscription_task
        assert output == ["new", "end"]

    async def test_multiple_subscribers(self):
        output_1, output_2 = [], []
        stream_start_event_1 = asyncio.Event()
        stream_start_event_2 = asyncio.Event()
        subscription_task_1 = subscription_task_2 = None
        async with util.Publisher() as publisher:
            subscription_task_1 = asyncio.create_task(
                self.stream_into_list(
                    publisher, output_1, stream_start_event_1
                )
            )
            await stream_start_event_1.wait()
            await publisher.append("a")
            subscription_task_2 = asyncio.create_task(
                self.stream_into_list(
                    publisher, output_2, stream_start_event_2
                )
            )
            await stream_start_event_2.wait()
            await publisher.append("b")
            await self.wait_for_list_content(output_1, ["a", "b"])
            await self.wait_for_list_content(output_2, ["b"])

        assert subscription_task_1
        assert subscription_task_2
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
        slow_task = fast_task = None
        async with util.Publisher() as publisher:
            slow_task = asyncio.create_task(
                self.stream_with_manual_continue(
                    publisher,
                    slow_element_queue,
                    slow_start_event,
                    continue_condition,
                )
            )
            fast_task = asyncio.create_task(
                self.stream_into_list(publisher, fast_output, fast_start_event)
            )
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
        assert slow_task
        assert fast_task
        await slow_task
        await fast_task
        assert fast_output == [1, 2, "end"]

    async def test_prunes_unneded_element_history(self):
        continue_condition = asyncio.Condition()
        element_queue = asyncio.Queue()
        stream_start_event = asyncio.Event()
        read_task = None
        async with util.Publisher() as publisher:
            read_task = asyncio.create_task(
                self.stream_with_manual_continue(
                    publisher,
                    element_queue,
                    stream_start_event,
                    continue_condition,
                )
            )
            await stream_start_event.wait()
            await publisher.append(1)
            await publisher.append(2)
            await publisher.append(3)
            assert await element_queue.get() == 1
            async with continue_condition:
                continue_condition.notify_all()
            await self.wait_until(lambda: len(publisher._history) == 2)  # pyright: ignore[reportPrivateUsage]

            assert await element_queue.get() == 2
            async with continue_condition:
                continue_condition.notify_all()
            await self.wait_until(lambda: len(publisher._history) == 1)  # pyright: ignore[reportPrivateUsage]

            assert await element_queue.get() == 3
            async with continue_condition:
                continue_condition.notify_all()
            await self.wait_until(lambda: len(publisher._history) == 1)  # pyright: ignore[reportPrivateUsage]
        assert read_task
        await read_task

    async def test_cleans_up_on_subscriber_exit(self):
        element_queue = asyncio.Queue()
        stream_start_event = asyncio.Event()

        async with util.Publisher() as publisher:
            read_task = asyncio.create_task(
                self.stream_with_manual_continue(
                    publisher,
                    element_queue,
                    stream_start_event,
                    asyncio.Condition(),
                )
            )
            await stream_start_event.wait()
            await publisher.append(1)
            await publisher.append(2)
            await self.wait_until(lambda: len(publisher._history) == 2)  # pyright: ignore[reportPrivateUsage]
            await self.wait_until(
                lambda: len(publisher._subscriber_next_seq) == 1  # pyright: ignore[reportPrivateUsage]
            )
            read_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await read_task
            await self.wait_until(lambda: len(publisher._history) == 0)  # pyright: ignore[reportPrivateUsage]
            await self.wait_until(
                lambda: len(publisher._subscriber_next_seq) == 0  # pyright: ignore[reportPrivateUsage]
            )

    async def test_subscriptions_exit_on_aexit(self):
        output = []
        stream_start_event = asyncio.Event()
        subscription_task = None
        async with util.Publisher() as publisher:
            subscription_task = asyncio.create_task(
                self.stream_into_list(publisher, output, stream_start_event)
            )
            await stream_start_event.wait()
            with pytest.raises(TimeoutError):
                async with asyncio.timeout(10**-3):
                    await asyncio.shield(subscription_task)
        assert subscription_task
        await subscription_task
        assert output == ["end"]

    async def test_cancellation_in_subscription_is_bubbled_up(self):
        async def read(publisher):
            async for _ in publisher.subscribe():
                pass

        async with util.Publisher() as publisher:
            task = asyncio.create_task(read(publisher), eager_start=True)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task


class TestImmediateValue:
    async def test_get_value(self):
        v = util.ImmediateValue(5)
        assert await v.value == 5


class TestFutureValue:
    async def test_set_and_get_value(self):
        v = util.FutureValue()
        v.value = 5
        assert await v.value == 5  # pyright: ignore[reportGeneralTypeIssues]

    async def test_get_blocks_until_set(self):
        v = util.FutureValue()
        get_task = asyncio.create_task(v.value)
        with pytest.raises(TimeoutError):
            async with asyncio.timeout(10**-3):
                await asyncio.shield(get_task)
        v.value = 5
        assert await v.value == 5  # pyright: ignore[reportGeneralTypeIssues]

    async def test_set_raises_if_already_set(self):
        v = util.FutureValue()
        v.value = 5
        with pytest.raises(ValueError):
            v.value = 5


class TestTtlCache:
    @pytest.fixture
    def event_loop_policy(self):
        return async_solipsism.EventLoopPolicy()

    async def test_get_caches_successful_refresh(self):
        refresh = um.AsyncMock(return_value="value")
        cache = util.TtlCache(60, refresh)
        assert await cache.get() == "value"
        await asyncio.sleep(59)
        assert await cache.get() == "value"
        refresh.assert_awaited_once_with()

    async def test_get_refreshes_after_expiry(self):
        refresh = um.AsyncMock(side_effect=["first", "second"])
        cache = util.TtlCache(60, refresh)
        assert await cache.get() == "first"
        await asyncio.sleep(60)
        assert await cache.get() == "second"
        assert refresh.await_count == 2

    async def test_concurrent_gets_share_refresh(self):
        refresh_started = asyncio.Event()
        finish_refresh = asyncio.Event()
        num_refreshes = 0

        async def refresh():
            nonlocal num_refreshes
            num_refreshes += 1
            refresh_started.set()
            await finish_refresh.wait()
            return "value"

        cache = util.TtlCache(60, refresh)
        get_tasks = [asyncio.create_task(cache.get()) for _ in range(2)]
        await refresh_started.wait()
        await asyncio.sleep(0)
        assert not get_tasks[0].done()
        assert not get_tasks[1].done()
        finish_refresh.set()

        assert await asyncio.gather(*get_tasks) == ["value", "value"]
        assert num_refreshes == 1

    async def test_initial_refresh_raises(self):
        refresh = um.AsyncMock(side_effect=RuntimeError("failed"))
        cache = util.TtlCache(60, refresh)
        with pytest.raises(RuntimeError, match="failed"):
            await cache.get()

    async def test_successive_refresh_raises(self):
        refresh = um.AsyncMock(side_effect=["value", RuntimeError("failed")])
        cache = util.TtlCache(60, refresh)

        assert await cache.get() == "value"
        await asyncio.sleep(60)
        with pytest.raises(RuntimeError, match="failed"):
            await cache.get()
        assert refresh.await_count == 2

    async def test_doesnt_reset_refresh_timer_after_failure(self):
        refresh = um.AsyncMock(
            side_effect=["first", RuntimeError("failed"), "second"]
        )
        cache = util.TtlCache(60, refresh)

        assert await cache.get() == "first"
        await asyncio.sleep(60)
        with pytest.raises(RuntimeError, match="failed"):
            await cache.get()
        assert await cache.get() == "second"
        assert refresh.await_count == 3


class MockContextManager:
    def __init__(self, counter, enter_result, exit_result=False):
        self._counter = counter
        self._enter_result = enter_result
        self._exit_result = exit_result
        self.calls = []

    async def __aenter__(self):
        self.calls.append(("__aenter__", (), next(self._counter)))
        return self._enter_result

    async def __aexit__(self, *args):
        self.calls.append(("__aexit__", args, next(self._counter)))
        return self._exit_result


class MockManager:
    def __init__(self):
        self.counter = it.count()

    def create_context_manager(self, enter_result, exit_result=False):
        return MockContextManager(self.counter, enter_result, exit_result)

    def create_factory(self, context_manager):
        calls = []

        async def factory():
            calls.append(("factory", (), next(self.counter)))
            return context_manager

        return factory, calls


class TestDependencyContextManager:
    async def test_raises_if_no_primary(self):
        cm = util.DependencyContextManager()
        with pytest.raises(ValueError):
            async with cm:
                pass

    async def test_raises_if_more_than_one_primary(self):
        manager = MockManager()
        cm = util.DependencyContextManager()
        cm.register_primary(manager.create_context_manager(None))
        with pytest.raises(ValueError):
            cm.register_primary(manager.create_context_manager(None))

    async def test_wraps_single_primary(self):
        manager = MockManager()
        primary_enter_result = object()
        cm = util.DependencyContextManager()
        primary = manager.create_context_manager(primary_enter_result)
        cm.register_primary(primary)
        async with cm as enter_result:
            assert enter_result is primary_enter_result
            assert primary.calls == [("__aenter__", (), 0)]
        assert primary.calls == [
            ("__aenter__", (), 0),
            ("__aexit__", (None, None, None), 1),
        ]

    async def test_enters_in_registration_order_and_exits_in_reverse_order(
        self,
    ):
        manager = MockManager()
        primary_result = object()
        primary = manager.create_context_manager(primary_result)
        dependency_1 = manager.create_context_manager(None)
        dependency_2 = manager.create_context_manager(None)

        cm = util.DependencyContextManager()
        cm.register_dependency(dependency_1)
        cm.register_primary(primary)
        cm.register_dependency(dependency_2)

        async with cm as enter_result:
            assert enter_result is primary_result

        assert dependency_1.calls == [
            ("__aenter__", (), 0),
            ("__aexit__", (None, None, None), 5),
        ]
        assert primary.calls == [
            ("__aenter__", (), 1),
            ("__aexit__", (None, None, None), 4),
        ]
        assert dependency_2.calls == [
            ("__aenter__", (), 2),
            ("__aexit__", (None, None, None), 3),
        ]

    async def test_creates_context_managers_from_factories_on_entry(self):
        manager = MockManager()
        primary = manager.create_context_manager("primary")
        dependency = manager.create_context_manager(None)
        primary_factory, primary_factory_calls = manager.create_factory(
            primary
        )
        dependency_factory, dependency_factory_calls = manager.create_factory(
            dependency
        )

        cm = util.DependencyContextManager()
        cm.register_primary(primary_factory)
        cm.register_dependency(dependency_factory)

        assert primary_factory_calls == []
        assert dependency_factory_calls == []
        async with cm as enter_result:
            assert enter_result == "primary"

        assert primary_factory_calls == [("factory", (), 0)]
        assert primary.calls == [
            ("__aenter__", (), 1),
            ("__aexit__", (None, None, None), 5),
        ]
        assert dependency_factory_calls == [("factory", (), 2)]
        assert dependency.calls == [
            ("__aenter__", (), 3),
            ("__aexit__", (None, None, None), 4),
        ]

    async def test_passes_exceptions_to_exit_handlers_in_reverse_order(self):
        manager = MockManager()
        primary = manager.create_context_manager(None)
        dependency = manager.create_context_manager(None)
        cm = util.DependencyContextManager()
        cm.register_primary(primary)
        cm.register_dependency(dependency)

        with pytest.raises(RuntimeError, match="failure"):
            async with cm:
                raise RuntimeError("failure")

        assert primary.calls[0] == ("__aenter__", (), 0)
        assert dependency.calls[0] == (
            "__aenter__",
            (),
            1,
        )
        for context_manager, expected_sequence in (
            (dependency, 2),
            (primary, 3),
        ):
            name, args, sequence = context_manager.calls[1]
            assert name == "__aexit__"
            assert args[0] is RuntimeError
            assert isinstance(args[1], RuntimeError)
            assert args[1].args == ("failure",)
            assert args[2] is not None
            assert sequence == expected_sequence

    async def test_allows_exit_handler_to_suppress_exception(self):
        manager = MockManager()
        primary = manager.create_context_manager(None)
        dependency = manager.create_context_manager(None, exit_result=True)
        cm = util.DependencyContextManager()
        cm.register_primary(primary)
        cm.register_dependency(dependency)

        async with cm:
            raise RuntimeError("suppressed")

        assert dependency.calls[0] == (  # pyright: ignore[reportUnreachable]
            "__aenter__",
            (),
            1,
        )
        name, args, sequence = dependency.calls[1]
        assert name == "__aexit__"
        assert args[0] is RuntimeError
        assert isinstance(args[1], RuntimeError)
        assert args[1].args == ("suppressed",)
        assert args[2] is not None
        assert sequence == 2
        assert primary.calls == [
            ("__aenter__", (), 0),
            ("__aexit__", (None, None, None), 3),
        ]

    async def test_cannot_register_or_enter_after_entry(self):
        manager = MockManager()
        cm = util.DependencyContextManager()
        cm.register_primary(manager.create_context_manager(None))

        async with cm:
            pass

        with pytest.raises(RuntimeError):
            cm.register_dependency(manager.create_context_manager(None))
        with pytest.raises(RuntimeError):
            async with cm:
                pass
