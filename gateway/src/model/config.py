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

import pathlib
import typing as t

import pydantic as pyd

from . import base


class MessageStoreConfig(base.BaseModel):
    # Default this to None, it will be set relative to the gateway's store_dir.
    base_dir: pathlib.Path = pyd.Field(default=None)


class MatrixConfig(base.BaseModel):
    homeserver: str
    username: str
    device_id: str
    # Default this to None, it will be set relative to the gateway's store_dir.
    store_dir: pathlib.Path = pyd.Field(default=None)


class GatewayConfig(base.BaseModel):
    files_base_dir: pathlib.Path
    message_store: MessageStoreConfig
    matrix: t.Optional[MatrixConfig]

    @pyd.model_validator(mode="after")
    def compute_message_store_base_dir(self) -> t.Self:
        self.message_store.base_dir = self.files_base_dir / "message_store"
        return self

    @pyd.model_validator(mode="after")
    def compute_matrix_store_dir(self) -> t.Self:
        if self.matrix:
            self.matrix.store_dir = self.files_base_dir / "matrix_nio"
        return self


class Config(base.BaseModel):
    config_version: t.Literal[0]
    gateway: GatewayConfig
