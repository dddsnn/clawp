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
import asyncio
import collections.abc as cl_abc
import json
import logging
import os
import pathlib
import shutil
import typing as t

import pydantic as pyd
import whenever as we

from . import message as msg
from . import model as mdl


class StoreError(Exception):
    pass


class StoreConcurrentError(StoreError, RuntimeError):
    """Raised when another message store already claimed the same directory."""


class StoreFormatError(StoreError, ValueError):
    """Raised when the file structure is invalid."""


TModel = t.TypeVar("TModel", bound=mdl.BaseModel | pyd.TypeAdapter)


class JsonlIO(t.Generic[TModel]):
    """
    IO on a JSONL file.

    Represents one JSONL file with a header.
    """

    def __init__(self, file_path: pathlib.Path, model_type: type[TModel]):
        self._logger = logging.getLogger(type(self).__name__)
        self._file_path = file_path
        self._model_type = model_type
        self._write_file: t.IO | None = None
        self._lock = asyncio.Lock()

    async def close(self) -> None:
        """
        Close the file.

        Closes the file if it was kept open for writing. Does nothing if not
        opened.
        """
        async with self._lock:
            if self._write_file is not None:
                await asyncio.to_thread(self._sync_close)
                self._write_file = None

    def _sync_close(self):
        try:
            self._write_file.close()
        except Exception:
            self._logger.exception(f"Error closing file {self._file_path}.")

    async def __aenter__(self) -> t.Self:
        return self

    async def __aexit__(self, *_) -> bool:
        await self.close()
        return False

    async def exists(self) -> bool:
        """Check whether the file exists."""
        async with self._lock:
            return await self._exists_locked()

    async def _exists_locked(self):
        return await asyncio.to_thread(self._file_path.exists)

    @property
    async def header(self) -> dict:
        """
        Read the file's header dict.

        If the header has an invalid format, StoreFormatError is raised. If the
        file doesn't exist, FileNotFoundError is raised.
        """
        async with self._lock:
            return await self._header_locked

    @property
    async def _header_locked(self):
        if not await self._exists_locked():
            raise FileNotFoundError(f"file {self._file_path} doesn't exist")
        return await asyncio.to_thread(self._sync_read_header)

    def _sync_read_header(self):
        with open(self._file_path, "r") as f:
            first_line = f.readline()
        if not first_line:
            raise StoreFormatError("empty file, missing header")
        try:
            h = json.loads(first_line)
        except json.JSONDecodeError as e:
            raise StoreFormatError("header is not valid JSON") from e
        if not isinstance(h, dict):
            raise StoreFormatError("header is not a dict")
        if "version" not in h:
            raise StoreFormatError("missing 'version' in header")
        if type(h["version"]) is not int:
            raise StoreFormatError("'version' is not an integer")
        return h

    async def create(self, header: dict) -> None:
        """
        Create the file with the given header.

        Also creates the parent directory if it doesn't exist.

        The header must have a version key, which must be an integer, or a
        ValueError is raised. If the file already exists, FileExistsError is
        raised.
        """
        async with self._lock:
            try:
                if type(header["version"]) is not int:
                    raise ValueError("'version' must be an int")
            except KeyError:
                raise ValueError("header must contain 'version'")
            await asyncio.to_thread(self._sync_create, header)

    def _sync_create(self, header):
        if self._file_path.exists():
            raise FileExistsError(f"file {self._file_path} already exists")
        self._file_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._file_path, "x") as f:
            f.write(json.dumps(header) + "\n")
            f.flush()
            os.fsync(f.fileno())

    async def append(self, model: TModel) -> None:
        """
        Append a model to the file.

        Dumps the given model as json and appends it as a line to the file.

        Uses a file opened for appending earlier if it exists, otherwise opens
        the file for appending and keeps it open for later. close() or
        __aexit__() must be called to close the file again. If the file
        doesn't, exist, FileNotFoundError is raised.
        """
        async with self._lock:
            if self._write_file is None:
                await asyncio.to_thread(self._sync_open_for_appending)
            assert self._write_file is not None
            await asyncio.to_thread(self._sync_append, model)

    def _sync_open_for_appending(self):
        with open(self._file_path):
            # Open to check that the file exists. Bubble up the
            # FileNotFoundError.
            pass
        self._write_file = open(self._file_path, "a")

    def _sync_append(self, model: TModel):
        self._write_file.write(model.model_dump_json() + "\n")
        self._write_file.flush()
        os.fsync(self._write_file.fileno())

    async def read_all(self) -> cl_abc.AsyncGenerator[TModel]:
        """
        Read all models from the file.

        Opens the file, skips past the header, and iterates over models parsed
        from the lines of the file.

        If a line doesn't parse successfully (including empty lines), raises a
        StoreFormatError. If the file doesn't exist, FileNotFoundError is
        raised.
        """
        async with self._lock:
            if not await self._exists_locked():
                raise FileNotFoundError(
                    f"file {self._file_path} doesn't exist"
                )
            lines = await self._read_lines()
            if not lines:
                raise StoreFormatError("missing header (empty file)")
            for line in lines[1:]:
                try:
                    yield self._validate_line(line)
                except pyd.ValidationError as e:
                    raise StoreFormatError(
                        f"invalid line in {self._file_path}: {line}"
                    ) from e

    def _validate_line(self, line: str) -> TModel:
        if isinstance(self._model_type, pyd.TypeAdapter):
            return self._model_type.validate_json(line)
        assert issubclass(self._model_type, pyd.BaseModel)
        return self._model_type.model_validate_json(line)

    async def _read_lines(self):
        return await asyncio.to_thread(self._sync_read_lines)

    def _sync_read_lines(self):
        with open(self._file_path, "r") as f:
            return f.readlines()

    async def upgrade_and_validate(
        self, upgraders: dict[int, t.Callable[[pathlib.Path], None]]
    ) -> None:
        """
        Upgrade and validate the file.

        Reads the file, parses the header, and tries to upgrade the file to the
        target version if necessary. The target version is N+1, where N is the
        maximum of the keys in the upgraders dict. If the version in the header
        is less than the target version, runs the necessary upgraders in
        sequence until the file has the target version. If the target version
        is less than the current version, raises a StoreFormatError (can't
        downgrade).

        After the upgrade, reads the entire file and validates that every model
        parses correctly. If the model on the last line doesn't parse
        correctly, a write error during an unclean shutdown is assumed. In this
        case, a warning is logged and the corrupt line removed. On any other
        validation error, a StoreFormatError is raised.

        If any error occurs in validation, StoreFormatError is raised. If the
        file doesn't exist, FileNotFoundError is raised.

        :param upgraders: A dictionary mapping a version number N to a function
            upgrading a file in place from version N to N+1.
        """
        async with self._lock:
            file_version = (await self._header_locked)["version"]
            target_version = max(upgraders.keys(), default=-1) + 1
            if file_version > target_version:
                raise StoreFormatError(
                    f"file has future version {file_version}"
                )
            await self._run_upgrade(upgraders, file_version, target_version)
            await self._validate_file()

    async def _run_upgrade(self, upgraders, from_version, target_version):
        for v in range(from_version, target_version):
            self._logger.info(
                f"Upgrading {self._file_path} from {v} to {v + 1}."
            )
            await asyncio.to_thread(upgraders[v], self._file_path)

    async def _validate_file(self):
        lines = await self._read_lines()
        for i, line in enumerate(lines[1:], start=1):
            try:
                self._validate_line(line)
            except pyd.ValidationError as e:
                is_last_line = i == len(lines) - 1
                if is_last_line:
                    self._logger.warning(
                        f"Last line in {self._file_path} is corrupt. Assuming "
                        "unclean shutdown, discarding line.",
                        exc_info=True,
                    )
                    await asyncio.to_thread(
                        self._delete_corrupted_last_line, line
                    )
                else:
                    raise StoreFormatError(
                        f"invalid line in {self._file_path}: {line}"
                    ) from e

    def _delete_corrupted_last_line(self, line: str):
        with self._file_path.open("r+") as f:
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
                raise StoreFormatError(
                    f"header of session file {self._file_path} is corrupt"
                )
            # Check that the last line is actually the one we expected.
            f.seek(pos + 1)
            line_in_file = f.read()
            if line_in_file != line:
                raise StoreFormatError(
                    f"attempted to delete corrupted line '{line}' in "
                    f"{self._file_path}, but last line was actually "
                    f"'{line_in_file}'"
                )
            # Go to the position where the last line starts and truncate from
            # there.
            f.seek(pos)
            f.truncate()


class MessageStore:
    """
    Persistent store for an agent's messages using JSONL files.

    The store uses a directory tree that mirrors the domain hierarchy:
    <base_dir>/sessions/<session_seq>.jsonl

    Each JSONL file starts with a header line containing the format version
    and session metadata, followed by one JSON object per message.

    The store keeps file handles open for active sessions to avoid repeated
    open/close overhead. All I/O is dispatched to a thread via
    asyncio.to_thread() to avoid blocking the event loop. The store assumes it
    owns its base_dir and has exclusive access to the files.

    MessageStore is an asynchronous context manager that takes control of the
    base_dir. When the context manager enters, it locks the directory (so only
    one instance may be active at any one time for this base directory) and
    checks base_dir for consistency. If it contains files with an older format,
    they are upgraded to the current one (a backup is created first).
    """

    VERSION = 0
    """
    Current message store format version.

    When the format changes, increment this and add a function to the
    _upgraders dictionary
    """

    _active_base_dirs = set()
    _active_base_dirs_lock = asyncio.Lock()

    def __init__(self, base_dir: pathlib.Path) -> None:
        self._logger = logging.getLogger(type(self).__name__)
        self._base_dir = base_dir
        self._open_ios: dict[int, JsonlIO[mdl.Message]] = {}
        self._open_ios_lock = asyncio.Lock()

    async def __aenter__(self) -> t.Self:
        async with self._active_base_dirs_lock:
            if self._base_dir in self._active_base_dirs:
                raise StoreConcurrentError(
                    f"another message store is already active for "
                    f"{self._base_dir}"
                )
            self._active_base_dirs.add(self._base_dir)
        await self._ensure_valid_store_format()
        return self

    async def __aexit__(self, *_) -> None:
        async with self._open_ios_lock:
            await self._close_ios()
        async with self._active_base_dirs_lock:
            self._active_base_dirs.discard(self._base_dir)

    async def _close_ios(self):
        close_tasks = set()
        for session_seq, io in self._open_ios.items():
            self._logger.debug(f"Closing {self._session_path(session_seq)}.")
            close_tasks.add(asyncio.create_task(io.close()))
        if close_tasks:
            _, pending = await asyncio.wait(close_tasks, timeout=10)
            if pending:
                self._logger.exception(
                    f"Timeout while closing files ({len(pending)} not done)."
                )
        self._open_ios.clear()

    def _sessions_dir(self) -> pathlib.Path:
        return self._base_dir / "sessions"

    def _session_path(self, session_seq: int) -> pathlib.Path:
        return self._sessions_dir() / f"{session_seq}.jsonl"

    async def append_message(
        self, session_seq: int, message: msg.Message
    ) -> None:
        """
        Append a message to a session file.

        The message will be serialized as JSON using its model property. If the
        session file doesn't exist yet, it is created first. If a session file
        needs to be created but now all previous sessions exist a
        MessageStoreFormatError is raised.
        """
        async with self._open_ios_lock:
            io = self._get_io(session_seq)
            try:
                await io.append(await message.model)
            except FileNotFoundError:
                await self._ensure_session_file(session_seq, io)
                await io.append(await message.model)

    def _get_io(self, session_seq):
        try:
            io = self._open_ios[session_seq]
        except KeyError:
            io = JsonlIO(
                self._session_path(session_seq), mdl.MessageTypeAdapter
            )
            self._open_ios[session_seq] = io
        return io

    async def _ensure_session_file(self, session_seq: int, io: JsonlIO):
        path = self._session_path(session_seq)
        for seq in range(session_seq):
            if not path.with_name(f"{seq}.jsonl").exists():
                raise StoreFormatError(
                    f"can't create session file {path}, because previous "
                    f"session {seq} doesn't exist"
                )
        header = {
            "version": self.VERSION,
            "session_seq": session_seq,
        }
        if not path.parent.exists():
            self._logger.info(f"Creating sessions directory {path.parent}.")
        await io.create(header)
        self._logger.info(f"Created new session file {path}.")

    async def read_session_messages(
        self, session_seq: int
    ) -> list[msg.Message]:
        """
        Read all messages from a session file.

        Returns a list of messages parsed from the file. Skips past the header
        (which has its own format) and parses each line as a message. If the
        session file doesn't exist, returns an empty list.

        Raises a MessageStoreFormatError if any line doesn't parse to a message
        (that includes empty lines).
        """
        io = self._get_io(session_seq)
        try:
            return [msg.Message.from_model(m) async for m in io.read_all()]
        except FileNotFoundError:
            return []

    def get_active_session_seq(self) -> int:
        """
        Get the active session sequence number.

        Returns the sequence number of the active session. This is 0 if there
        are no sessions with messages yet.
        """
        sessions_dir = self._sessions_dir()
        if not sessions_dir.exists():
            return 0
        seqs = set()
        for entry in sessions_dir.iterdir():
            if not entry.is_file():
                self._logger.warning(
                    "Unexpected directory in sessions directory "
                    f"{sessions_dir}."
                )
                continue
            try:
                assert entry.name.endswith(".jsonl")
                seqs.add(int(entry.name.removesuffix(".jsonl")))
            except Exception:
                self._logger.warning(
                    f"Unexpected file {entry} in sessions directory "
                    f"{sessions_dir}.",
                    exc_info=True,
                )
                continue
        active_session_seq = max(seqs, default=0)
        if active_session_seq and active_session_seq + 1 != len(seqs):
            self._logger.warning(
                f"Missing session sequence numbers in {sorted(seqs)}."
            )
        return active_session_seq

    def get_session_message_store(
        self, session_seq: int
    ) -> "SessionMessageStore":
        """Get a message store specific to a session."""
        return SessionMessageStore(session_seq, self)

    async def _ensure_valid_store_format(self) -> None:
        """
        Ensure that base_dir is consistent and valid.

        Creates the base_dir and sessions directory if necessary. Goes through
        the sessions directory and checks that the session files in it are
        consistent. This is the case if

        - the session_seq in the session file's header is consistent with the
          file name
        - all session files have the same version number
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
        if not self._sessions_dir().exists():
            self._logger.info(
                f"Sessions directory {self._sessions_dir()} doesn't exist "
                "yet, creating it."
            )
            self._sessions_dir().mkdir(parents=True, exist_ok=True)
        session_file_versions = set()
        prev_seq = None
        for seq, _ in self._list_all_session_files():
            if prev_seq is None and seq != 0:
                raise StoreFormatError(
                    "session sequence numbers don't start at 0"
                )
            if prev_seq is not None and prev_seq + 1 != seq:
                raise StoreFormatError(
                    f"broken session sequence numbers after {prev_seq}"
                )
            prev_seq = seq
            session_file_version = await self._ensure_valid_session_format(seq)
            session_file_versions.add(session_file_version)
        if len(session_file_versions) > 1:
            raise StoreFormatError(
                "inconsistent message store with "
                f"{len(session_file_versions)} different versions"
            )
        version_on_disk = next(iter(session_file_versions), self.VERSION)
        if version_on_disk < self.VERSION:
            self._logger.info(
                f"Found store with version {version_on_disk}, upgrading to "
                f"{self.VERSION}."
            )
            await self._upgrade_files(from_version=version_on_disk)
        elif version_on_disk > self.VERSION:
            raise StoreFormatError(
                f"store on disk has higher version {version_on_disk} than "
                "known the this implementation, unable to downgrade"
            )
        else:
            # Re-validate with upgrader if already current just to ensure all
            # jsonl lines parse.
            if version_on_disk == self.VERSION:
                for seq, file in list(self._list_all_session_files()):
                    io = JsonlIO(file, mdl.MessageTypeAdapter)
                    await io.upgrade_and_validate(self._upgraders)
            self._logger.debug(
                f"Found valid message store at {self._base_dir} with version "
                f"{self.VERSION}."
            )

    def _list_all_session_files(
        self,
    ) -> cl_abc.Generator[tuple[int, pathlib.Path]]:
        for seq in range(self.get_active_session_seq() + 1):
            session_file = self._session_path(seq)
            if session_file.is_file():
                yield seq, session_file
            elif seq != 0:
                self._logger.warning(f"Missing session file {session_file}.")

    async def _ensure_valid_session_format(self, seq: int):
        path = self._session_path(seq)
        io = JsonlIO(path, mdl.MessageTypeAdapter)
        try:
            header_dict = await io.header
            assert isinstance(header_dict["session_seq"], int)
        except Exception as e:
            raise StoreFormatError("invalid header format") from e
        if seq != header_dict["session_seq"]:
            raise StoreFormatError(
                f"inconsistent session file {path}: directory suggests "
                f"session {seq}, but file header says "
                f"{header_dict['session_seq']}"
            )
        return header_dict["version"]

    async def _upgrade_files(self, from_version: int) -> None:
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
            f"{we.Instant.now()}"
        )
        backup_directory = self._base_dir.parent / backup_directory_name
        await asyncio.to_thread(
            shutil.copytree, self._base_dir, backup_directory
        )
        for _, file in list(self._list_all_session_files()):
            assert file.is_file()
            io = JsonlIO(file, mdl.MessageTypeAdapter)
            await io.upgrade_and_validate(self._upgraders)

    _upgraders: dict[int, t.Callable[[pathlib.Path], None]] = {}
    """
    Registry of upgrade functions, keyed by the version they upgrade from.

    Each function takes a file containing data in version N and transforms it
    in place to version N+1. All upgraders stay in the codebase so that any
    previous version can be upgraded by running them in sequence.
    """

    for version_number in range(VERSION):
        assert version_number in _upgraders


class SessionMessageStore:
    """
    Persistent store for session messages.

    This is a wrapper around MessageStore which makes the underlying methods
    available for one specific session.
    """

    def __init__(self, session_seq: int, message_store: MessageStore) -> None:
        self._session_seq = session_seq
        self._message_store = message_store

    async def append_message(self, message: msg.Message) -> None:
        return await self._message_store.append_message(
            self._session_seq, message
        )

    async def read_session_messages(self) -> list[msg.Message]:
        return await self._message_store.read_session_messages(
            self._session_seq
        )


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
        start_time: t.Optional[we.Instant],
        end_time: t.Optional[we.Instant],
        search_term: t.Optional[str],
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


class JsonlMemoryStore(MemoryStore):
    """Memory store backed by a jsonl file."""

    VERSION = 0
    """Current message store format version."""

    def __init__(self, base_dir: pathlib.Path) -> None:
        file_path = base_dir / "memory.jsonl"
        self._io = JsonlIO(file_path, mdl.Memory)

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
        start_time: t.Optional[we.Instant] = None,
        end_time: t.Optional[we.Instant] = None,
        search_term: t.Optional[str] = None,
    ) -> cl_abc.AsyncGenerator[mdl.Memory, None]:
        start_time = start_time or we.Instant.MIN
        end_time = end_time or we.Instant.MAX

        def is_relevant(memory):
            if not start_time <= memory.time <= end_time:
                return False
            if (
                search_term is not None
                and search_term.lower() not in memory.content.lower()
            ):
                return False
            return True

        try:
            async for memory in self._io.read_all():
                if is_relevant(memory):
                    yield memory
        except FileNotFoundError:
            return
