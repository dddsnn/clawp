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
import model as mdl


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
    <base_dir>/agents/<agent_id>/sessions/<session_seq>.jsonl

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
        self._open_files_lock = asyncio.Lock()

    async def __aenter__(self) -> t.Self:
        try:
            await asyncio.wait_for(self._message_store_lock.acquire(), 10**-2)
        except asyncio.TimeoutError:
            raise MessageStoreConcurrentError(
                "another message store is already active")
        self._ensure_valid_store_format()
        return self

    async def __aexit__(self, *_) -> None:
        async with self._open_files_lock:
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

    def _agents_dir(self) -> pathlib.Path:
        return self._base_dir / "agents"

    def _agent_dir(self, agent_id: uuid.UUID) -> pathlib.Path:
        return self._agents_dir() / str(agent_id)

    def _sessions_dir(self, agent_id: uuid.UUID) -> pathlib.Path:
        return self._agent_dir(agent_id) / "sessions"

    def _session_path(
            self, agent_id: uuid.UUID, session_seq: int) -> pathlib.Path:
        return self._sessions_dir(agent_id) / f"{session_seq}.jsonl"

    async def append_message(
            self, agent_id: uuid.UUID, session_seq: int,
            message: msg.Message) -> None:
        """
        Append a message to a session file.

        The message will be serialized as JSON using its model property. If the
        session file doesn't exist yet, it is created first. If a session file
        needs to be created but now all previous sessions exist for the agent,
        a MessageStoreFormatError is raised.
        """
        async with self._open_files_lock:
            await asyncio.to_thread(
                self._sync_append_message, agent_id, session_seq, await
                message.model)

    def _sync_append_message(
            self, agent_id: uuid.UUID, session_seq: int,
            message_model: mdl.Message):
        path = self._session_path(agent_id, session_seq)
        try:
            f = self._open_files[path]
        except KeyError:
            self._ensure_session_file(agent_id, session_seq)
            assert path.exists()
            f = open(path, "a")
            self._open_files[path] = f
        f.write(message_model.model_dump_json() + "\n")
        f.flush()
        os.fsync(f.fileno())

    def _ensure_session_file(self, agent_id: uuid.UUID, session_seq: int):
        path = self._session_path(agent_id, session_seq)
        if path.exists():
            return
        for seq in range(session_seq):
            if not path.with_name(f"{seq}.jsonl").exists():
                raise MessageStoreFormatError(
                    f"can't create session file {path}, because previous "
                    f"session {seq} doesn't exist")
        header = {
            "version": self.VERSION,
            "agent_id": str(agent_id),
            "session_seq": session_seq,}
        if not path.parent.exists():
            self._logger.info(f"Creating sessions directory {path.parent}.")
            path.parent.mkdir(parents=True)
        with open(path, "w") as f:
            f.write(json.dumps(header) + "\n")
            f.flush()
            os.fsync(f.fileno())
        self._logger.info(f"Created new session file {path}.")

    async def read_session_messages(
            self, agent_id: uuid.UUID, session_seq: int) -> list[msg.Message]:
        """
        Read all messages from a session file.

        Returns a list of messages parsed from the file. Skips past the header
        (which has its own format) and parses each line as a message. If the
        session file doesn't exist, returns an empty list.

        Raises a MessageStoreFormatError if any line doesn't parse to a message
        (that includes empty lines).
        """
        path = self._session_path(agent_id, session_seq)
        json_lines = await asyncio.to_thread(self._sync_read_messages, path)
        # Do the parsing in the async method here because we need an event loop
        # for the messages' from_model() (which wants to schedule a task for
        # the StreamableList).
        messages = []
        for i, json_line in enumerate(json_lines):
            try:
                message = msg.Message.from_model(
                    mdl.MessageTypeAdapter.validate_json(json_line))
                messages.append(message)
            except pyd.ValidationError:
                # A truncated last line likely means the app crashed
                # mid-write. Log a warning and discard it.
                if i == len(json_lines) - 1:
                    self._logger.warning(
                        f"Last line ({json_line}) in session file {path} is "
                        "corrupt. Assuming an unclean shutdown, ignoring the "
                        "line and deleting it from the file.", exc_info=True)
                    await asyncio.to_thread(
                        self._delete_corrupted_last_line, path, json_line)
                else:
                    raise MessageStoreFormatError(
                        f"invalid line in session file {path}: {json_line}")
        return messages

    def _sync_read_messages(self, path):
        try:
            with open(path, "r") as f:
                lines = f.readlines()
        except FileNotFoundError:
            return []
        # Skip the header (first line).
        return lines[1:]

    def _delete_corrupted_last_line(self, path: pathlib.Path, line: str):
        with path.open("r+") as f:
            # Move the pointer to the end of the file and remember where it is.
            f.seek(0, os.SEEK_END)
            pos_past_end = f.tell() + 1
            # Read the file backwards until we find a newline (except if it is
            # the last character).
            pos = f.tell()
            while pos > 0 and f.read(1) != "\n" and pos != pos_past_end:
                pos -= 1
                f.seek(pos)
            if pos == 0:
                raise MessageStoreFormatError(
                    f"header of session file {path} is corrupt")
            # Check that the last line is actually the one we expected.
            f.seek(pos + 1)
            line_in_file = f.read()
            if line_in_file != line:
                raise MessageStoreFormatError(
                    f"attempted to delete corrupted line '{line}' in {path}, "
                    f"but last line was actually '{line_in_file}'")
            # Go to the position where the last line starts and truncate from
            # there.
            f.seek(pos)
            f.truncate()

    def list_agents(self) -> list[uuid.UUID]:
        """
        List all agent IDs.

        Returns an empty list if the store has no agents yet.
        """
        try:
            entries = self._agents_dir().iterdir()
        except FileNotFoundError:
            return []
        ids = []
        for entry in entries:
            if not entry.is_dir():
                self._logger.warning(f"Unexpected file {entry} in directory.")
            try:
                ids.append(uuid.UUID(entry.name))
            except ValueError:
                self._logger.exception(
                    f"Subdirectory {entry} is not a valid UUID.")
                continue
        return sorted(ids)

    def get_active_session_seq(self, agent_id: uuid.UUID) -> t.Optional[int]:
        """
        Get the active session sequence number.

        Returns the sequence number of the active session, or None if there are
        no sessions for this agent yet.
        """
        sessions_dir = self._sessions_dir(agent_id)
        if not sessions_dir.exists():
            return None
        seqs = set()
        for entry in sessions_dir.iterdir():
            if not entry.is_file():
                self._logger.warning(
                    "Unexpected directory in sessions directory "
                    f"{sessions_dir}.")
                continue
            try:
                assert entry.name.endswith(".jsonl")
                seqs.add(int(entry.name.removesuffix(".jsonl")))
            except Exception:
                self._logger.warning(
                    f"Unexpected file {entry} in sessions directory "
                    f"{sessions_dir}.", exc_info=True)
                continue
        active_session_seq = max(seqs, default=None)
        if active_session_seq and active_session_seq + 1 != len(seqs):
            self._logger.warning(
                f"Missing session sequence numbers in {sorted(seqs)}.")
        return active_session_seq

    def _list_all_sessions(self) -> cl_abc.Generator[tuple[uuid.UUID, int]]:
        for agent_id in self.list_agents():
            active_session_seq = self.get_active_session_seq(agent_id)
            if active_session_seq is None:
                self._logger.warning(f"No sessions for agent {agent_id}")
                continue
            for seq in range(active_session_seq + 1):
                session_file = self._session_path(agent_id, seq)
                if session_file.is_file():
                    yield agent_id, seq
                else:
                    self._logger.warning(
                        f"Missing session file {session_file}.")

    def _list_all_session_files(self) -> cl_abc.Generator[pathlib.Path]:
        for agent_id, seq in self._list_all_sessions():
            path = self._session_path(agent_id, seq)
            assert path.is_file()
            yield path

    def get_agent_message_store(
            self, agent_id: uuid.UUID) -> "AgentMessageStore":
        """Get a message store specific to an agent."""
        return AgentMessageStore(agent_id, self)

    def _ensure_valid_store_format(self) -> None:
        """
        Ensure that base_dir is consistent and valid.

        Goes through the session directory of every agent and checks that the
        session files in it are consistent. This is the case if

        - the agent_id and session_seq in the session file's header is
          consistent with the directory/file name
        - all session files have the version number
        - the session files' version number is not greater than
          MessageStore.VERSION

        Additionally, in each session directory the following must hold:

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
        sessions_by_agent = it.groupby(
            self._list_all_sessions(), key=lambda agt_seq: agt_seq[0])
        for agent_id, agt_seq in sessions_by_agent:
            prev_seq = None
            for _, seq in agt_seq:
                if prev_seq is None and seq != 0:
                    raise MessageStoreFormatError(
                        f"session sequence numbers of {agent_id} doesn't "
                        "start at 0")
                if prev_seq is not None and prev_seq + 1 != seq:
                    raise MessageStoreFormatError(
                        f"broken session sequence numbers of {agent_id} after "
                        f"{prev_seq}")
                prev_seq = seq
                session_file_version = self._ensure_valid_session_format(
                    agent_id, seq)
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

    def _ensure_valid_session_format(self, agent_id: uuid.UUID, seq: int):
        path = self._session_path(agent_id, seq)
        try:
            with path.open() as f:
                header_dict = json.loads(f.readline())
            assert isinstance(header_dict["version"], int)
            assert isinstance(header_dict["session_seq"], int)
            header_dict["agent_id"] = uuid.UUID(header_dict["agent_id"])
        except Exception as e:
            raise MessageStoreFormatError("invalid header format") from e
        if agent_id != header_dict["agent_id"]:
            raise MessageStoreFormatError(
                f"inconsistent session file {path}: directory suggests agent "
                f"{agent_id}, but file header says {header_dict['agent_id']}")
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


class AgentMessageStore:
    """
    Persistent store for agent messages.

    This is a wrapper around MessageStore which makes the underlying methods
    available for one specific agent.
    """
    def __init__(
            self, agent_id: uuid.UUID, message_store: MessageStore) -> None:
        self._agent_id = agent_id
        self._message_store = message_store

    async def append_message(
            self, session_seq: int, message: msg.Message) -> None:
        return await self._message_store.append_message(
            self._agent_id, session_seq, message)

    async def read_session_messages(self,
                                    session_seq: int) -> list[msg.Message]:
        return await self._message_store.read_session_messages(
            self._agent_id, session_seq)

    def get_active_session_seq(self) -> t.Optional[int]:
        return self._message_store.get_active_session_seq(self._agent_id)

    def get_session_message_store(
            self, session_seq: int) -> "SessionMessageStore":
        """Get a message store specific to a session."""
        return SessionMessageStore(
            self._agent_id, session_seq, self._message_store)


class SessionMessageStore:
    """
    Persistent store for session messages.

    This is a wrapper around MessageStore which makes the underlying methods
    available for one specific session of an agent.
    """
    def __init__(
            self, agent_id: uuid.UUID, session_seq: int,
            message_store: MessageStore) -> None:
        self._agent_id = agent_id
        self._session_seq = session_seq
        self._message_store = message_store

    async def append_message(self, message: msg.Message) -> None:
        return await self._message_store.append_message(
            self._agent_id, self._session_seq, message)

    async def read_session_messages(self) -> list[msg.Message]:
        return await self._message_store.read_session_messages(
            self._agent_id, self._session_seq)
