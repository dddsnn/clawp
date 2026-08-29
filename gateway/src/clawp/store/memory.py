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
import collections.abc as cl_abc
import pathlib

import whenever as we

from .. import model as mdl
from . import base


class MemoryStore(abc.ABC):
    """A persistent store for memory logs."""

    @abc.abstractmethod
    async def log_memory(self, content: str) -> None:
        """
        Log a memory.

        The memory is persisted with the current time and can later be found
        via search_memory().
        """
        raise NotImplementedError

    @abc.abstractmethod
    async def search_memory(
        self,
        *,
        start_time: we.Instant | None,
        end_time: we.Instant | None,
        search_term: str | None,
    ) -> cl_abc.AsyncGenerator[mdl.Memory]:
        """
        Search memories.

        Asynchronously iterates over all stored memories matching the given
        search criteria. Memories are iterated in ascending order of their
        time.

        start_time and end_time filter for memories in the
        time range they bound. If one or both are None, memories are not
        filtered by start/end time.

        If search_term is given, a simple case-insensitive substring match is
        made to filter results.

        If no filters are given, all memories are returned.
        """
        raise NotImplementedError
        yield  # pyright: ignore[reportUnreachable] (to make it a generator)


class JsonlMemoryStore(MemoryStore):
    """Memory store backed by a jsonl file."""

    VERSION = 0
    """Current message store format version."""

    def __init__(self, base_dir: pathlib.Path) -> None:
        file_path = base_dir / "memory.jsonl"
        self._io = base.JsonlIO(file_path, mdl.Memory)

    async def log_memory(self, content: str) -> None:
        memory = mdl.Memory(time=we.Instant.now(), content=content)
        try:
            await self._io.append(memory)
        except FileNotFoundError:
            await self._io.create({"version": self.VERSION})
            await self._io.append(memory)

    async def search_memory(
        self,
        *,
        start_time: we.Instant | None = None,
        end_time: we.Instant | None = None,
        search_term: str | None = None,
    ) -> cl_abc.AsyncGenerator[mdl.Memory]:
        start_time = start_time or we.Instant.MIN
        end_time = end_time or we.Instant.MAX

        def is_relevant(memory):
            if not start_time <= memory.time <= end_time:
                return False
            search_term_is_relevant = (
                search_term is None
                or search_term.lower() in memory.content.lower()
            )
            return search_term_is_relevant

        try:
            async for memory in self._io.read_all():
                if is_relevant(memory):
                    yield memory
        except FileNotFoundError:
            return
