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
import pydantic_settings as pyd_set


class BaseSettings(pyd_set.BaseSettings):
    model_config = pyd_set.SettingsConfigDict(
        env_prefix="CLAWP_", env_prefix_target="alias")


class OpenRouterConfig(BaseSettings):
    api_key: str = pyd.Field(alias="OPENROUTER_API_KEY")
    model: str


class MatrixConfig(BaseSettings):
    homeserver: str
    username: str
    password: str = pyd.Field(alias="MATRIX_PASSWORD")
    device_id: str
    # Default this to None, it will be set relative to the gateway's store_dir.
    store_dir: pathlib.Path = pyd.Field(default=None, validate_default=False)


class ApiConfig(BaseSettings):
    host: pyd.IPvAnyAddress
    port: int
    log_level: t.Literal["critical", "error", "warning", "info", "debug",
                         "trace"]


class GatewayConfig(BaseSettings):
    files_base_dir: pathlib.Path
    """
    The base directory for all of the gateway's files.

    All persistent files the gateway needs will be stored below this path.
    """
    openrouter: OpenRouterConfig
    matrix: t.Optional[MatrixConfig]
    api: ApiConfig

    @pyd.computed_field
    @property
    def agents_base_dir(self) -> pathlib.Path:
        """The base directory for agent-specific data."""
        return self.files_base_dir / "agents"

    @pyd.model_validator(mode="after")
    def compute_matrix_store_dir(self) -> t.Self:
        if self.matrix:
            self.matrix.store_dir = self.files_base_dir / "matrix_nio"
        return self


class Config(BaseSettings):
    config_version: t.Literal[0]
    gateway: GatewayConfig
