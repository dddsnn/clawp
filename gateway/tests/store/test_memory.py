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

import json

import pytest
import whenever as we
from hamcrest import (
    all_of,
    assert_that,
    contains_exactly,
    has_properties,
    instance_of,
)

from clawp import model as mdl
from clawp import store

from .test_base import read_file_content


def memory(**kwargs):
    return all_of(instance_of(mdl.Memory), has_properties(**kwargs))


class TestJsonlMemoryStore:
    @pytest.fixture
    def base_dir(self, tmp_path):
        return tmp_path / "memory"

    @pytest.fixture
    def memory_store(self, base_dir):
        return store.JsonlMemoryStore(base_dir)

    async def test_log_memory_creates_file(self, memory_store, base_dir):
        await memory_store.log_memory("test event")
        lines = read_file_content(base_dir / "memory.jsonl")
        assert len(lines) == 2
        data = json.loads(lines[1])
        assert data["content"] == "test event"
        assert "id" in data
        assert "time" in data

    async def test_search_memory_no_filters(self, memory_store):
        await memory_store.log_memory("event 1")
        await memory_store.log_memory("event 2")
        results = [m async for m in memory_store.search_memory()]
        assert_that(
            results,
            contains_exactly(
                memory(content="event 1"), memory(content="event 2")
            ),
        )

    async def test_search_memory_by_start_time(self, memory_store):
        t1 = we.Instant.from_utc(2026, 1, 1, 12, 0, 0)
        t2 = we.Instant.from_utc(2026, 1, 2, 12, 0, 0)
        t3 = we.Instant.from_utc(2026, 1, 3, 12, 0, 0)

        with we.patch_current_time(t1, keep_ticking=False):
            await memory_store.log_memory("event 1")
        with we.patch_current_time(t2, keep_ticking=False):
            await memory_store.log_memory("event 2")
        with we.patch_current_time(t3, keep_ticking=False):
            await memory_store.log_memory("event 3")

        results = [m async for m in memory_store.search_memory(start_time=t2)]
        assert_that(
            results,
            contains_exactly(
                memory(content="event 2"), memory(content="event 3")
            ),
        )

    async def test_search_memory_by_end_time(self, memory_store):
        t1 = we.Instant.from_utc(2026, 1, 1, 12, 0, 0)
        t2 = we.Instant.from_utc(2026, 1, 2, 12, 0, 0)
        t3 = we.Instant.from_utc(2026, 1, 3, 12, 0, 0)

        with we.patch_current_time(t1, keep_ticking=False):
            await memory_store.log_memory("event 1")
        with we.patch_current_time(t2, keep_ticking=False):
            await memory_store.log_memory("event 2")
        with we.patch_current_time(t3, keep_ticking=False):
            await memory_store.log_memory("event 3")

        results = [m async for m in memory_store.search_memory(end_time=t2)]
        assert_that(
            results,
            contains_exactly(
                memory(content="event 1"), memory(content="event 2")
            ),
        )

    async def test_search_memory_by_start_and_end_time(self, memory_store):
        t1 = we.Instant.from_utc(2026, 1, 1, 12, 0, 0)
        t2 = we.Instant.from_utc(2026, 1, 2, 12, 0, 0)
        t3 = we.Instant.from_utc(2026, 1, 3, 12, 0, 0)

        with we.patch_current_time(t1, keep_ticking=False):
            await memory_store.log_memory("event 1")
        with we.patch_current_time(t2, keep_ticking=False):
            await memory_store.log_memory("event 2")
        with we.patch_current_time(t3, keep_ticking=False):
            await memory_store.log_memory("event 3")

        results = [
            m
            async for m in memory_store.search_memory(
                start_time=t2, end_time=t2
            )
        ]
        assert_that(results, contains_exactly(memory(content="event 2")))

    async def test_search_memory_by_search_term(self, memory_store):
        await memory_store.log_memory("hello world")
        await memory_store.log_memory("testing memories")
        await memory_store.log_memory("goodbye")

        results = [
            m async for m in memory_store.search_memory(search_term="hello")
        ]
        assert_that(results, contains_exactly(memory(content="hello world")))

    async def test_search_memory_by_search_term_is_case_insensitive(
        self, memory_store
    ):
        await memory_store.log_memory("Hello World")
        await memory_store.log_memory("testing memories")
        await memory_store.log_memory("hello again")

        results = [
            m async for m in memory_store.search_memory(search_term="HeLlO")
        ]
        assert_that(
            results,
            contains_exactly(
                memory(content="Hello World"), memory(content="hello again")
            ),
        )

    async def test_search_memory_raises_format_error_on_corrupt_line(
        self, memory_store, base_dir
    ):
        await memory_store.log_memory("valid event")
        # Manually append an invalid line.
        with (base_dir / "memory.jsonl").open("a") as f:
            f.write("this is not json\n")
        with pytest.raises(store.StoreFormatError):
            [m async for m in memory_store.search_memory()]

    async def test_search_memory_raises_format_error_on_empty_line(
        self, memory_store, base_dir
    ):
        await memory_store.log_memory("valid event")
        # Manually append an empty line.
        with (base_dir / "memory.jsonl").open("a") as f:
            f.write("\n")
        with pytest.raises(store.StoreFormatError):
            [m async for m in memory_store.search_memory()]
