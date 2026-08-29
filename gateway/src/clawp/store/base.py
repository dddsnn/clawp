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
import json
import logging
import os
import pathlib
import typing as t

import pydantic as pyd


class StoreError(Exception):
    pass


class StoreConcurrentError(StoreError, RuntimeError):
    """Raised when another message store already claimed the same directory."""


class StoreFormatError(StoreError, ValueError):
    """Raised when the file structure is invalid."""


class JsonlIO[ModelType: pyd.BaseModel]:
    """
    IO on a JSONL file.

    Represents one JSONL file with a header.
    """

    def __init__(
        self,
        file_path: pathlib.Path,
        model_type: type[ModelType] | pyd.TypeAdapter[ModelType],
    ):
        self._logger = logging.getLogger(type(self).__name__)
        self._file_path = file_path
        self._model_type = model_type
        self._write_file: t.IO[str] | None = None
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
        assert self._write_file is not None
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
    async def header(self) -> dict[str, t.Any]:
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

    async def create(self, header: dict[str, t.Any]) -> None:
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

    async def append(self, model: ModelType) -> None:
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
        self._write_file = open(self._file_path, "a")  # noqa: SIM115

    def _sync_append(self, model: ModelType):
        assert self._write_file is not None
        self._write_file.write(model.model_dump_json() + "\n")
        self._write_file.flush()
        os.fsync(self._write_file.fileno())

    async def read_all(self) -> cl_abc.AsyncGenerator[ModelType]:
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

    def _validate_line(self, line: str) -> ModelType:
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
