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
import logging
import pathlib
import typing as t

from . import model as mdl


class GatewayStateManager:
    """
    Manager for global state of the gateway.

    This is an asynchronous context manager that loads and stores a file
    containing global state of the app on __aenter__/__aexit__. The state can
    be accessed and mutated while the app is running.
    """

    def __init__(self, base_dir: pathlib.Path) -> None:
        self._logger = logging.getLogger(type(self).__name__)
        self._state_file = base_dir / "gateway_state.json"
        self._state: mdl.GatewayState = None  # pyright: ignore[reportAttributeAccessIssue]

    async def __aenter__(self) -> t.Self:
        await asyncio.to_thread(self._load_or_create_state)
        return self

    async def __aexit__(self, *args) -> t.Literal[False]:
        await asyncio.to_thread(self._write_state)
        return False

    def _load_or_create_state(self) -> None:
        assert self._state is None
        if self._state_file.exists() and not self._state_file.is_file():
            raise ValueError(f"{self._state_file} exists but is not a file")
        elif not self._state_file.is_file():
            self._logger.info(
                f"State file {self._state_file} doesn't exist, creating new "
                "gateway state."
            )
            self._state = mdl.GatewayState()
        else:
            self._state = mdl.GatewayState.model_validate_json(
                self._state_file.read_text()
            )
            self._logger.info(f"Loaded state from file {self._state_file}.")

    def _write_state(self) -> None:
        assert self._state is not None
        self._state_file.write_text(self._state.model_dump_json())

    @property
    def state(self) -> mdl.GatewayState:
        assert self._state is not None
        return self._state
