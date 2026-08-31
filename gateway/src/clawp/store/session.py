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
import logging
import pathlib
import shutil
import typing as t

import pydantic as pyd
import whenever as we

from .. import message as msg
from .. import model as mdl
from . import base


class SessionsStore:
    """
    Persistent store for all of an agent's sessions using JSONL files.

    The store creates a subdirectory for each session within its base
    directory, which contain <base_dir>/<session_seq>/messages.jsonl and
    <base_dir>/<session_seq>/state.json.

    The messages.jsonl file contains the messages in the session starts with a
    header line containing the format version and session metadata, followed by
    one JSON object per message. The state.json file contains mutable state
    data about the session.

    File handles for the .jsonl files are kept open for active sessions to
    avoid repeated open/close overhead. All I/O is dispatched to a thread via
    asyncio.to_thread() to avoid blocking the event loop. The store assumes it
    owns its base_dir and has exclusive access to the files.

    SessionsStore is an asynchronous context manager that takes control of the
    base_dir. When the context manager enters, it locks the directory (so only
    one instance may be active at any one time for this base directory) and
    checks base_dir for consistency. If it contains files with an older format,
    they are upgraded to the current one (a backup is created first). State
    files are written on aexit.
    """

    VERSION = 0
    """
    Current message store format version.

    When the format changes, increment this and add a function to the
    _upgraders dictionary
    """

    _active_base_dirs = set()  # noqa: RUF012
    _active_base_dirs_lock = asyncio.Lock()

    def __init__(self, base_dir: pathlib.Path) -> None:
        self._logger = logging.getLogger(type(self).__name__)
        self._base_dir = base_dir
        self._open_ios: dict[int, base.JsonlIO[mdl.Message]] = {}
        self._open_ios_lock = asyncio.Lock()
        self._states: dict[int, mdl.SessionState] = {}

    async def __aenter__(self) -> t.Self:
        async with self._active_base_dirs_lock:
            if self._base_dir in self._active_base_dirs:
                raise base.StoreConcurrentError(
                    f"another message store is already active for "
                    f"{self._base_dir}"
                )
            self._active_base_dirs.add(self._base_dir)
        await self._ensure_valid_store_format()
        return self

    async def __aexit__(self, *_) -> None:
        await asyncio.to_thread(self._write_states_sync)
        async with self._open_ios_lock:
            await self._close_ios()
        async with self._active_base_dirs_lock:
            self._active_base_dirs.discard(self._base_dir)

    def _write_states_sync(self) -> None:
        for session_seq, state in self._states.items():
            self._state_file_path(session_seq).write_text(
                state.model_dump_json()
            )

    async def _close_ios(self):
        close_tasks = set()
        for session_seq, io in self._open_ios.items():
            self._logger.debug(
                f"Closing {self._message_file_path(session_seq)}."
            )
            close_tasks.add(asyncio.create_task(io.close()))
        if close_tasks:
            _, pending = await asyncio.wait(close_tasks, timeout=10)
            if pending:
                self._logger.exception(
                    f"Timeout while closing files ({len(pending)} not done)."
                )
        self._open_ios.clear()

    def _session_dir(self, session_seq: int) -> pathlib.Path:
        return self._base_dir / str(session_seq)

    def _message_file_path(self, session_seq: int) -> pathlib.Path:
        return self._session_dir(session_seq) / "messages.jsonl"

    def _state_file_path(self, session_seq: int) -> pathlib.Path:
        return self._session_dir(session_seq) / "state.json"

    async def append_message(
        self, session_seq: int, message: msg.Message[msg.MessageMetadata]
    ) -> None:
        """
        Append a message to a session file.

        The message will be serialized as JSON using its model property. If the
        session file doesn't exist yet, it is created first. If a session file
        needs to be created but now all previous sessions exist a
        StoreFormatError is raised.
        """
        async with self._open_ios_lock:
            io = self._get_io(session_seq)
            try:
                await io.append(await message.model)
            except FileNotFoundError:
                await self._ensure_session_file(session_seq, io)
                await io.append(await message.model)

    def _get_io(self, session_seq) -> base.JsonlIO[mdl.Message]:
        try:
            io = self._open_ios[session_seq]
        except KeyError:
            io = base.JsonlIO(
                self._message_file_path(session_seq),
                pyd.TypeAdapter(mdl.Message),
            )
            self._open_ios[session_seq] = io
        return io

    async def _ensure_session_file(
        self, session_seq: int, io: base.JsonlIO[mdl.Message]
    ):
        await self._ensure_session_dir(session_seq)
        header = {
            "version": self.VERSION,
            "session_seq": session_seq,
        }
        await io.create(header)
        self._logger.info(
            f"Created new session file {self._message_file_path(session_seq)}."
        )

    async def _ensure_session_dir(self, session_seq: int):
        dir = self._session_dir(session_seq)
        if dir.is_dir():
            return
        assert session_seq not in self._states
        for seq in range(session_seq):
            if not (dir.parent / str(seq)).is_dir():
                raise base.StoreFormatError(
                    f"can't create session file in directory {dir}, because "
                    f"previous session {seq} doesn't exist"
                )
        self._logger.info(
            f"Creating sessions directory {dir} with default session state."
        )
        dir.mkdir(parents=True)
        self._states[session_seq] = mdl.SessionState()
        self._state_file_path(session_seq).write_text(
            self._states[session_seq].model_dump_json()
        )

    async def load_or_create(
        self, session_seq: int
    ) -> tuple[mdl.SessionState, list[msg.Message[msg.MessageMetadata]]]:
        """
        Load or create state and messages from a session directory.

        Returns a tuple (state, messages), where state is the session state and
        messages is a list of messages parsed from the messages file. Skips
        past the header (which has its own format) and parses each line as a
        message. If the session file doesn't exist, returns an empty list.

        If the session directory doesn't exist, it is created along with a
        default state file (but all previous session directories must exist, or
        a StoreFormatError is raised).

        Raises a StoreFormatError if the state file is invalid or any line of
        the message file doesn't parse (this includes empty lines).
        """
        await self._ensure_session_dir(session_seq)
        try:
            state = self._get_state(session_seq)
        except FileNotFoundError as e:
            raise base.StoreFormatError(
                f"missing state file for session {session_seq}"
            ) from e
        except pyd.ValidationError as e:
            raise base.StoreFormatError(
                f"invalid state file for session {session_seq}"
            ) from e
        io = self._get_io(session_seq)
        try:
            messages = [msg.Message.from_model(m) async for m in io.read_all()]
        except FileNotFoundError:
            messages = []
        return state, messages

    def _get_state(self, session_seq: int) -> mdl.SessionState:
        try:
            return self._states[session_seq]
        except KeyError:
            state = mdl.SessionState.model_validate_json(
                self._state_file_path(session_seq).read_text()
            )
            return self._states.setdefault(session_seq, state)

    def get_active_session_seq(self) -> int:
        """
        Get the active session sequence number.

        Returns the sequence number of the active session. This is 0 if there
        are no sessions with messages yet.
        """
        if not self._base_dir.exists():
            return 0
        seqs = set()
        for entry in self._base_dir.iterdir():
            if not entry.is_dir():
                self._logger.warning(
                    "Unexpected non_directory in sessions directory "
                    f"{self._base_dir}."
                )
                continue
            try:
                seqs.add(int(entry.name))
            except Exception:
                self._logger.warning(
                    f"Unexpected directory {entry} in sessions directory "
                    f"{self._base_dir}.",
                    exc_info=True,
                )
                continue
        active_session_seq = max(seqs, default=0)
        if active_session_seq and active_session_seq + 1 != len(seqs):
            self._logger.warning(
                f"Missing session sequence numbers in {sorted(seqs)}."
            )
        return active_session_seq

    def for_session(self, session_seq: int) -> SessionStore:
        """Get a store specific to a session."""
        return SessionStore(session_seq, self)

    async def _ensure_valid_store_format(self) -> None:
        """
        Ensure that base_dir is consistent and valid.

        Creates the base_dir if necessary. Goes through all sessions
        directories and checks that the session files in it are consistent.
        This is the case if

        - the state file exists and is valid
        - the session_seq in the message file's header is consistent with the
          file name
        - all message files have the same version number
        - the message files' version number is not greater than
          SessionsStore.VERSION

        Additionally, the following must hold:

        - session sequence numbers start at 0
        - no session sequence numbers are missing

        If base_dir doesn't exist, it is created. If the message files have a
        previous version, they are upgraded to the current one using the
        functions in the _upgraders dictionary.

        If any inconsistencies are found, a StoreFormatError is raised.
        """
        if not self._base_dir.exists():
            self._logger.info(
                f"Sessions directory {self._base_dir} doesn't exist "
                "yet, creating it."
            )
            self._base_dir.mkdir(parents=True, exist_ok=True)
        session_file_versions = set()
        prev_seq = None
        for seq in self._list_all_session_seqs():
            if prev_seq is None and seq != 0:
                raise base.StoreFormatError(
                    "session sequence numbers don't start at 0"
                )
            if prev_seq is not None and prev_seq + 1 != seq:
                raise base.StoreFormatError(
                    f"broken session sequence numbers after {prev_seq}"
                )
            prev_seq = seq
            self._ensure_valid_state(seq)
            session_file_version = await self._ensure_valid_session_format(seq)
            session_file_versions.add(session_file_version)
        if len(session_file_versions) > 1:
            raise base.StoreFormatError(
                "inconsistent session store with "
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
            raise base.StoreFormatError(
                f"store on disk has higher version {version_on_disk} than "
                "known the this implementation, unable to downgrade"
            )
        else:
            # Re-validate with upgrader if already current just to ensure all
            # jsonl lines parse.
            if version_on_disk == self.VERSION:
                for seq in list(self._list_all_session_seqs()):
                    io = base.JsonlIO(
                        self._message_file_path(seq),
                        pyd.TypeAdapter[mdl.Message](mdl.Message),
                    )
                    await io.upgrade_and_validate(self._upgraders)
            self._logger.debug(
                f"Found valid message store at {self._base_dir} with version "
                f"{self.VERSION}."
            )

    def _list_all_session_seqs(self) -> cl_abc.Generator[int]:
        for seq in range(self.get_active_session_seq() + 1):
            session_dir = self._session_dir(seq)
            if session_dir.is_dir():
                yield seq
            elif seq != 0:
                self._logger.warning(
                    f"Missing session diretory {session_dir}."
                )

    def _ensure_valid_state(self, seq: int):
        try:
            mdl.SessionState.model_validate_json(
                self._state_file_path(seq).read_text()
            )
        except FileNotFoundError as e:
            raise base.StoreFormatError(
                f"missing state file for session {seq}"
            ) from e
        except Exception as e:
            raise base.StoreFormatError(
                f"invalid state file for session {seq}"
            ) from e

    async def _ensure_valid_session_format(self, seq: int):
        message_file = self._message_file_path(seq)
        io = base.JsonlIO(
            message_file, pyd.TypeAdapter[mdl.Message](mdl.Message)
        )
        try:
            header_dict = await io.header
            assert isinstance(header_dict["session_seq"], int)
        except Exception as e:
            raise base.StoreFormatError("invalid header format") from e
        if seq != header_dict["session_seq"]:
            raise base.StoreFormatError(
                f"inconsistent session message file {message_file}: directory "
                f"suggests session {seq}, but file header says "
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
        for seq in list(self._list_all_session_seqs()):
            message_file = self._message_file_path(seq)
            assert message_file.is_file()
            io = base.JsonlIO(
                message_file, pyd.TypeAdapter[mdl.Message](mdl.Message)
            )
            await io.upgrade_and_validate(self._upgraders)

    _upgraders: dict[int, t.Callable[[pathlib.Path], None]] = {}  # noqa: RUF012
    """
    Registry of upgrade functions, keyed by the version they upgrade from.

    Each function takes a file containing data in version N and transforms it
    in place to version N+1. All upgraders stay in the codebase so that any
    previous version can be upgraded by running them in sequence.
    """

    for version_number in range(VERSION):
        assert version_number in _upgraders


class SessionStore:
    """
    Persistent store for session messages.

    This is just a wrapper around SessionsStore which makes the underlying
    methods available for one specific session.
    """

    def __init__(
        self, session_seq: int, sessions_store: SessionsStore
    ) -> None:
        self._session_seq = session_seq
        self._sessions_store = sessions_store

    async def append_message(
        self, message: msg.Message[msg.MessageMetadata]
    ) -> None:
        return await self._sessions_store.append_message(
            self._session_seq, message
        )

    async def load_or_create(
        self,
    ) -> tuple[mdl.SessionState, list[msg.Message[msg.MessageMetadata]]]:
        return await self._sessions_store.load_or_create(self._session_seq)
