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
import collections.abc as cl_abc
import itertools as it
import json
import logging
import os
import pathlib
import shutil
import typing as t
import uuid

import pydantic as pyd
import whenever as we

import message as msg
import model


class MessageStoreError(Exception):
    pass


class MessageStoreConcurrentError(MessageStoreError, RuntimeError):
    """Raised when another message store already claimed the same directory."""


class MessageStoreFormatError(MessageStoreError, ValueError):
    """Raised when the file structure is invalid."""


class MessageStore:
    """
    Persistent store for messages using JSONL files.

    The store uses a directory tree that mirrors the domain hierarchy:
    <base_dir>/assistants/<assistant_id>/consciousnesses/<consciousness_id>/
    sessions/<session_seq>.jsonl

    Each JSONL file starts with a header line containing the format version
    and session metadata, followed by one JSON object per message.

    The store keeps file handles open for active sessions to avoid repeated
    open/close overhead. All I/O is dispatched to a thread via
    asyncio.to_thread to avoid blocking the event loop.

    MessageStore is an asynchronous context manager that takes control of the
    base_dir. When the context manager enters, it locks the directory (so only
    one instance may be active at any one time) and checks base_dir for
    consistency. If it contains files with an older format, they are upgraded
    to the current one (a backup is created first).
    """

    VERSION = 0
    """
    Current message store format version.

    When the format changes, increment this and add a function to the
    _upgraders dictionary
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
            raise MessageStoreConcurrentError(
                "another message store is already active")
        self._ensure_valid_store_format()
        return self

    async def __aexit__(self, *_) -> None:
        await self._close_files()
        self._message_store_lock.release()

    async def _close_files(self):
        close_tasks = set()
        for path, f in self._open_files.items():
            self._logger.debug(f"Closing {path}.")
            close_tasks.add(
                asyncio.create_task(
                    asyncio.to_thread(self._safe_close_file, f)))
        if close_tasks:
            _, pending = await asyncio.wait(close_tasks, timeout=10)
            if pending:
                self._logger.exception(
                    f"Timeout while closing files ({len(pending)} not done).")
        self._open_files.clear()

    def _safe_close_file(self, f: t.IO):
        try:
            f.close()
        except Exception:
            self._logger.exception(f"Error closing file {f}.")

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
            "version": self.VERSION,
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
            session_seq: int, message: msg.Message) -> None:
        """
        Append a message to a session file.

        The message will be serialized as JSON using its model property. The
        session must have been created first, or a FileNotFoundError is raised.
        """
        path = self._session_path(assistant_id, consciousness_id, session_seq)
        await asyncio.to_thread(self._sync_append, path, await message.model)

    def _sync_append(self, path, model):
        f = self._open_files.get(path)
        if f is None:
            if not path.exists():
                raise FileNotFoundError(f"session file does not exist: {path}")
            f = open(path, "a")
            self._open_files[path] = f
        f.write(model.model_dump_json() + "\n")
        f.flush()
        os.fsync(f.fileno())

    async def read_session_messages(
            self, assistant_id: uuid.UUID, consciousness_id: uuid.UUID,
            session_seq: int) -> list[msg.Message]:
        """
        Read all messages from a session file.

        Returns a list of messages parsed from the file. Skips past the header
        (which has its own format) and parses each line as a message.

        Raises FileNotFoundError if the session file doesn't exist. Raises a
        MessageStoreFormatError if any line doesn't parse to a message (that
        includes empty lines).
        """
        path = self._session_path(assistant_id, consciousness_id, session_seq)
        json_lines = await asyncio.to_thread(self._sync_read_messages, path)
        # Do the parsing in the async method here because we need an event loop
        # for the messages' from_model() (which wants to schedule a task for
        # the StreamableList).
        messages = []
        for i, json_line in enumerate(json_lines):
            try:
                message = msg.Message.from_model(
                    model.MessageTypeAdapter.validate_json(json_line))
                messages.append(message)
            except pyd.ValidationError:
                # A truncated last line likely means the app crashed
                # mid-write. Log a warning and discard it.
                if i == len(json_lines) - 1:
                    self._logger.warning(
                        f"Discarding truncated last line in {path}.",
                        exc_info=True)
                else:
                    raise MessageStoreFormatError(
                        f"invalid line in session file {path}: {json_line}")
        return messages

    def _sync_read_messages(self, path):
        if not path.exists():
            raise FileNotFoundError(f"session file does not exist: {path}")
        with open(path, "r") as f:
            lines = f.readlines()
        # Skip the header (first line).
        return lines[1:]

    def list_assistants(self) -> list[uuid.UUID]:
        """
        List all assistant IDs.

        Returns an empty list if the store has no assistants yet.
        """
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

    def list_consciousnesses(self, assistant_id: uuid.UUID) -> list[uuid.UUID]:
        """
        List all consciousness IDs for an assistant.

        Returns an empty list if the assistant has no consciousnesses yet.
        """
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

    def list_sessions(
            self, assistant_id: uuid.UUID,
            consciousness_id: uuid.UUID) -> list[int]:
        """
        List all session sequence numbers for a consciousness.

        Returns a sorted list of sequence numbers, or an empty list if the
        consciousness has no sessions yet.
        """
        sessions_dir = self._sessions_dir(assistant_id, consciousness_id)
        if not sessions_dir.exists():
            return []
        seqs = []
        for entry in sessions_dir.iterdir():
            if not entry.is_file():
                self._logger.warning(
                    "Unexpected directory in sessions directory "
                    f"{sessions_dir}.")
                continue
            try:
                assert entry.name.endswith(".jsonl")
                seqs.append(int(entry.name.removesuffix(".jsonl")))
            except Exception:
                self._logger.warning(
                    f"Unexpected file {entry} in sessions directory "
                    f"{sessions_dir}.", exc_info=True)
                continue
        return sorted(seqs)

    def _list_all_sessions(
            self) -> cl_abc.Generator[tuple[uuid.UUID, uuid.UUID, int]]:
        for assistant_id in self.list_assistants():
            for consciousness_id in self.list_consciousnesses(assistant_id):
                for seq in self.list_sessions(assistant_id, consciousness_id):
                    yield assistant_id, consciousness_id, seq

    def _list_all_session_files(self) -> cl_abc.Generator[pathlib.Path]:
        for assistant_id, consciousness_id, seq in self._list_all_sessions():
            path = self._session_path(assistant_id, consciousness_id, seq)
            assert path.is_file()
            yield path

    def _ensure_valid_store_format(self) -> None:
        """
        Ensure that base_dir is consistent and valid.

        Goes through each consciousness directory of every assistant and checks
        that the session files in it are consistent. This is the case if

        - the assistant_id, consciouness_id and session_seq in the session
          file's header is consistent with the directory/file name
        - all session files have the version number
        - the session files' version number is not greater than
          MessageStore.VERSION

        Additionally, in each consiousness directory the following must hold:

        - session sequence numbers start at 0
        - no session sequence numbers are missing

        If base_dir doesn't exist, it is created. If the session files have a
        previous version, they are upgraded to the current one using the
        functions in the _upgraders dictionary.

        If any inconsistencies are found, a MessageStoreFormatError is raised.
        """
        if not self._base_dir.exists():
            self._logger.info(
                f"Message store directory {self._base_dir} doesn't exist yet, "
                "creating it.")
            self._base_dir.mkdir()
        session_file_versions = set()
        sessions_by_consciousness = it.groupby(
            self._list_all_sessions(), key=lambda acs: (acs[0], acs[1]))
        for (assistant_id, consciousness_id), acs in sessions_by_consciousness:
            prev_seq = None
            for _, _, seq in acs:
                if prev_seq is None and seq != 0:
                    raise MessageStoreFormatError(
                        f"session sequence numbers of {assistant_id}:"
                        f"{consciousness_id} doesn't start at 0")
                if prev_seq is not None and prev_seq + 1 != seq:
                    raise MessageStoreFormatError(
                        "broken session sequence numbers of "
                        f"{assistant_id}:{consciousness_id} after "
                        f"{prev_seq}")
                prev_seq = seq
                session_file_version = self._ensure_valid_session_format(
                    assistant_id, consciousness_id, seq)
                session_file_versions.add(session_file_version)
        if len(session_file_versions) > 1:
            raise MessageStoreFormatError(
                "inconsistent message store with "
                f"{len(session_file_versions)} different versions")
        version_on_disk = next(iter(session_file_versions), self.VERSION)
        if version_on_disk < self.VERSION:
            self._logger.info(
                f"Found store with version {version_on_disk}, upgrading to "
                f"{self.VERSION}.")
            self._upgrade_files(from_version=version_on_disk)
        elif version_on_disk > self.VERSION:
            raise MessageStoreFormatError(
                f"store on disk has higher version {version_on_disk} than "
                "known the this implementation, unable to downgrade")
        else:
            self._logger.debug(
                f"Found valid message store at {self._base_dir} with version "
                f"{self.VERSION}.")

    def _ensure_valid_session_format(
            self, assistant_id: uuid.UUID, consciousness_id: uuid.UUID,
            seq: int):
        path = self._session_path(assistant_id, consciousness_id, seq)
        try:
            with path.open() as f:
                header_dict = json.loads(f.readline())
            assert isinstance(header_dict["version"], int)
            assert isinstance(header_dict["session_seq"], int)
            for uuid_key in ["assistant_id", "consciousness_id"]:
                header_dict[uuid_key] = uuid.UUID(header_dict[uuid_key])
        except Exception as e:
            raise MessageStoreFormatError("invalid header format") from e
        if assistant_id != header_dict["assistant_id"]:
            raise MessageStoreFormatError(
                f"inconsistent session file {path}: directory suggests "
                f"assistant {assistant_id}, but file header says "
                f"{header_dict['assistant_id']}")
        if consciousness_id != header_dict["consciousness_id"]:
            raise MessageStoreFormatError(
                f"inconsistent session file {path}: directory suggests "
                f"consciousness {consciousness_id}, but file header says "
                f"{header_dict['consciousness_id']}")
        if seq != header_dict["session_seq"]:
            raise MessageStoreFormatError(
                f"inconsistent session file {path}: directory suggests "
                f"session {seq}, but file header says "
                f"{header_dict['session_seq']}")
        return header_dict["version"]

    def _upgrade_files(self, from_version: int) -> None:
        """
        Upgrade the on-disk data to the current version.

        Upgrades all session files to the current format. Uses the functions in
        the _upgraders dictionary to upgrade each file version by version.
        Before the upgrade, the entire old base_dir is backed up to a sibling
        directory.

        The base_dir must exist, and all session files must have a valid format
        according to from_version.
        """
        assert from_version < self.VERSION
        assert self._base_dir.is_dir()
        backup_directory_name = (
            f"backup_{self._base_dir.name}_version_{from_version}_"
            f"{we.Instant.now()}")
        backup_directory = self._base_dir.parent / backup_directory_name
        shutil.copytree(self._base_dir, backup_directory)
        for file in list(self._list_all_session_files()):
            assert file.is_file()
            for version in range(from_version, self.VERSION):
                upgrader = self._upgraders[version]
                self._logger.debug(
                    f"Upgrading {file} from {version} to {version+1}.")
                upgrader(file)

    _upgraders: dict[int, t.Callable[[pathlib.Path], None]] = {}
    """
    Registry of upgrade functions, keyed by the version they upgrade from.

    Each function takes the base directory and transforms the on-disk data from
    version N to N+1. All upgraders stay in the codebase so that any previous
    version can be upgraded by running them in sequence.
    """

    for version_number in range(VERSION):
        assert version_number in _upgraders
