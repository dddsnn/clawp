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
import json
import logging
import os
import pathlib
import typing as t
import uuid

VERSION = 1
"""
Current message store format version.

When the format changes, increment this and add an _upgrade_from_<old>
function.
"""

_UPGRADERS: dict[int, t.Callable[[pathlib.Path], None]] = {}
"""
Registry of upgrade functions, keyed by the version they upgrade from.

Each function takes the base directory and transforms the on-disk data from
version N to N+1. All upgraders stay in the codebase so that any previous
version can be upgraded by running them in sequence.
"""


class MessageStore:
    """
    Persistent store for assistant messages using JSONL files.

    The store uses a directory tree that mirrors the domain hierarchy:
    <base_dir>/assistants/<assistant_id>/consciousnesses/<consciousness_id>/
    sessions/<session_seq>.jsonl

    Each JSONL file starts with a header line containing the format version
    and session metadata, followed by one JSON object per message.

    The store keeps file handles open for active sessions to avoid repeated
    open/close overhead. All I/O is dispatched to a thread via
    asyncio.to_thread to avoid blocking the event loop.

    MessageStore is an asynchronous context manager that takes control of the
    base_dir. Only one instance may be active at any one time.
    """
    _message_store_lock = asyncio.Lock()

    def __init__(self, base_dir: pathlib.Path) -> None:
        self._logger = logging.getLogger(type(self).__name__)
        self._base_dir = base_dir
        self._open_files: dict[pathlib.Path, t.IO] = {}

    async def __aenter__(self) -> t.Self:
        try:
            await asyncio.wait_for(self._message_store_lock.acquire(), 10**-2)
        except asyncio.TimeoutError:
            raise RuntimeError("another MessageStore is already active")
        return self

    async def __aexit__(self, *_) -> None:
        for path, f in self._open_files.items():
            self._logger.debug(f"Closing {path}.")
            await asyncio.to_thread(f.close)
        self._open_files.clear()
        self._message_store_lock.release()

    def _assistants_dir(self) -> pathlib.Path:
        return self._base_dir / "assistants"

    def _assistant_dir(self, assistant_id: uuid.UUID) -> pathlib.Path:
        return self._assistants_dir() / str(assistant_id)

    def _consciousnesses_dir(self, assistant_id: uuid.UUID) -> pathlib.Path:
        return self._assistant_dir(assistant_id) / "consciousnesses"

    def _consciousness_dir(
            self, assistant_id: uuid.UUID,
            consciousness_id: uuid.UUID) -> pathlib.Path:
        return self._consciousnesses_dir(assistant_id) / str(consciousness_id)

    def _sessions_dir(
            self, assistant_id: uuid.UUID,
            consciousness_id: uuid.UUID) -> pathlib.Path:
        return self._consciousness_dir(
            assistant_id, consciousness_id) / "sessions"

    def _session_path(
            self, assistant_id: uuid.UUID, consciousness_id: uuid.UUID,
            session_seq: int) -> pathlib.Path:
        return self._sessions_dir(
            assistant_id, consciousness_id) / f"{session_seq}.jsonl"

    async def create_session(
            self, assistant_id: uuid.UUID, consciousness_id: uuid.UUID,
            session_seq: int) -> None:
        """
        Create a new session file with a header.

        Creates the directory tree if it doesn't exist. Raises FileExistsError
        if the session file already exists.
        """
        path = self._session_path(assistant_id, consciousness_id, session_seq)
        header = {
            "version": VERSION,
            "assistant_id": str(assistant_id),
            "consciousness_id": str(consciousness_id),
            "session_seq": session_seq,}
        await asyncio.to_thread(self._sync_create_session, path, header)

    def _sync_create_session(self, path, header):
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            raise FileExistsError(f"session file already exists: {path}")
        with open(path, "w") as f:
            f.write(json.dumps(header) + "\n")
            f.flush()
            os.fsync(f.fileno())

    async def append_message(
            self, assistant_id: uuid.UUID, consciousness_id: uuid.UUID,
            session_seq: int, message: dict) -> None:
        """
        Append a message to a session file.

        The message is a dict that will be serialized as JSON. The session
        must have been created first, or a FileNotFoundError is raised.
        """
        path = self._session_path(assistant_id, consciousness_id, session_seq)
        line = json.dumps(message)
        await asyncio.to_thread(self._sync_append, path, line)

    def _sync_append(self, path, line):
        f = self._open_files.get(path)
        if f is None:
            if not path.exists():
                raise FileNotFoundError(f"session file does not exist: {path}")
            f = open(path, "a")
            self._open_files[path] = f
        f.write(line + "\n")
        f.flush()
        os.fsync(f.fileno())

    async def read_session_header(
            self, assistant_id: uuid.UUID, consciousness_id: uuid.UUID,
            session_seq: int) -> dict:
        """
        Read the header of a session file.

        Returns the header dict. Raises FileNotFoundError if the session
        doesn't exist. Raises a ValueError if the header has an invalid format.
        """
        path = self._session_path(assistant_id, consciousness_id, session_seq)
        return await asyncio.to_thread(self._sync_read_header, path)

    def _sync_read_header(self, path):
        if not path.exists():
            raise FileNotFoundError(f"session file does not exist: {path}")
        with open(path, "r") as f:
            header_line = f.readline()
        try:
            header_dict = json.loads(header_line)
            assert isinstance(header_dict["version"], int)
            assert isinstance(header_dict["session_seq"], int)
            for uuid_key in ["assistant_id", "consciousness_id"]:
                header_dict[uuid_key] = uuid.UUID(header_dict[uuid_key])
        except Exception as e:
            raise ValueError("invalid header format") from e
        return header_dict

    async def read_session_messages(
            self, assistant_id: uuid.UUID, consciousness_id: uuid.UUID,
            session_seq: int) -> list[dict]:
        """
        Read all messages from a session file.

        Returns a list of message dicts (excluding the header). Raises
        FileNotFoundError if the session doesn't exist.
        """
        path = self._session_path(assistant_id, consciousness_id, session_seq)
        return await asyncio.to_thread(self._sync_read_messages, path)

    def _sync_read_messages(self, path):
        if not path.exists():
            raise FileNotFoundError(f"session file does not exist: {path}")
        with open(path, "r") as f:
            lines = f.readlines()
        # Skip the header (first line).
        messages = []
        for i, line in enumerate(lines[1:], start=2):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                messages.append(json.loads(stripped))
            except json.JSONDecodeError:
                # A truncated last line likely means the app crashed
                # mid-write. Log a warning and discard it.
                if i == len(lines):
                    self._logger.warning(
                        f"Discarding truncated last line in {path}.")
                else:
                    raise
        return messages

    async def list_assistants(self) -> list[uuid.UUID]:
        """
        List all assistant IDs.

        Returns an empty list if the store has no assistants yet.
        """
        return await asyncio.to_thread(self._sync_list_assistants)

    def _sync_list_assistants(self):
        try:
            entries = self._assistants_dir().iterdir()
        except FileNotFoundError:
            return []
        assistant_ids = []
        for entry in entries:
            if not entry.is_dir():
                self._logger.warning(
                    f"Unexpected file {entry} in assistants directory.")
            try:
                assistant_ids.append(uuid.UUID(entry.name))
            except ValueError:
                self._logger.exception(
                    f"Assistant subdirectory {entry} is not a valid UUID.")
                continue
        return sorted(assistant_ids)

    async def list_consciousnesses(self,
                                   assistant_id: uuid.UUID) -> list[uuid.UUID]:
        """
        List all consciousness IDs for an assistant.

        Returns an empty list if the assistant has no consciousnesses yet.
        """
        return await asyncio.to_thread(
            self._sync_list_consciousnesses, assistant_id)

    def _sync_list_consciousnesses(self, assistant_id):
        try:
            entries = self._consciousnesses_dir(assistant_id).iterdir()
        except FileNotFoundError:
            return []
        consciousness_ids = []
        for entry in entries:
            if not entry.is_dir():
                self._logger.warning(
                    f"Unexpected file {entry} in consciousnesses directory.")
            try:
                consciousness_ids.append(uuid.UUID(entry.name))
            except ValueError:
                self._logger.exception(
                    f"Consciousness subdirectory {entry} is not a valid UUID.")
                continue
        return sorted(consciousness_ids)

    async def list_sessions(
            self, assistant_id: uuid.UUID,
            consciousness_id: uuid.UUID) -> list[int]:
        """
        List all session sequence numbers for a consciousness.

        Returns a sorted list of sequence numbers, or an empty list if the
        consciousness has no sessions yet.
        """
        return await asyncio.to_thread(
            self._sync_list_sessions, assistant_id, consciousness_id)

    def _sync_list_sessions(self, assistant_id, consciousness_id):
        sessions_dir = self._sessions_dir(assistant_id, consciousness_id)
        if not sessions_dir.exists():
            return []
        seqs = []
        for entry in sessions_dir.iterdir():
            if not entry.is_file():
                self._logger.warning(
                    "Ignoring nexpected directory in sessions directory "
                    f"{sessions_dir}.")
                continue
            try:
                assert entry.name.endswith(".jsonl")
                seqs.append(int(entry.name.removesuffix(".jsonl")))
            except Exception:
                self._logger.warning(
                    f"Ignoring unexpected file {entry} in sessions directory "
                    f"{sessions_dir}.", exc_info=True)
                continue
        return sorted(seqs)


def upgrade(base_dir: pathlib.Path) -> None:
    """
    Upgrade the on-disk data to the current version.

    Reads the version from the store's version file and applies all necessary
    upgrade functions in sequence. If no version file exists, assumes this is
    a fresh store and writes the current version.

    This is synchronous and should be called before the async event loop
    starts (or in a thread).
    """
    version_path = base_dir / "version"
    if not base_dir.exists():
        base_dir.mkdir(parents=True, exist_ok=True)
        _write_version(version_path, VERSION)
        return
    if not version_path.exists():
        # Existing directory without a version file. Assume version 1 (the
        # first version that could exist on disk).
        disk_version = 1
    else:
        disk_version = int(version_path.read_text().strip())
    if disk_version == VERSION:
        _write_version(version_path, VERSION)
        return
    if disk_version > VERSION:
        raise RuntimeError(
            f"on-disk version {disk_version} is newer than the current "
            f"version {VERSION}, refusing to downgrade")
    for v in range(disk_version, VERSION):
        upgrader = _UPGRADERS.get(v)
        if upgrader is None:
            raise RuntimeError(f"no upgrader from version {v} to {v + 1}")
        upgrader(base_dir)
    _write_version(version_path, VERSION)


def _write_version(version_path: pathlib.Path, version: int) -> None:
    version_path.write_text(str(version) + "\n")
