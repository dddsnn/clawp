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
import os
import pathlib
import typing as t

import pydantic as pyd
import pydantic_settings as pyd_set
import whenever as we

from . import base


class Account(base.BaseModel, abc.ABC):
    type: t.Literal["matrix"]

    @pyd.computed_field
    @property
    @abc.abstractmethod
    def id(self) -> str:
        raise NotImplementedError


class BaseSettings(pyd_set.BaseSettings):
    model_config = pyd_set.SettingsConfigDict(
        env_prefix="CLAWP_", env_prefix_target="alias")


class ModelConfig(BaseSettings):
    name: str
    doom_loop_max_requests: int
    message_send_timeout: we.TimeDelta
    request_timeout: we.TimeDelta

    @pyd.model_validator(mode="after")
    def check_timeout_proportions(self) -> t.Self:
        if self.message_send_timeout >= self.request_timeout:
            raise ValueError(
                "request timeout must be greater than message send timeout")
        return self


class OpenRouterConfig(BaseSettings):
    api_key: str = pyd.Field(alias="OPENROUTER_API_KEY")
    model: ModelConfig


class MatrixAccountConfig(BaseSettings, Account):
    type: t.Literal["matrix"] = "matrix"
    homeserver: str
    username: str
    # Default this to None, it will be loaded from env by MatrixConfig.
    password: str = pyd.Field(
        default=None, validate_default=False, exclude=True)
    device_id: str

    @property
    def id(self) -> str:
        return self.username


class MatrixConfig(BaseSettings):
    # Default this to None, it will be set relative to the gateway's store_dir.
    store_dir: pathlib.Path = pyd.Field(default=None, validate_default=False)
    accounts: list[MatrixAccountConfig]

    @pyd.model_validator(mode="before")
    @classmethod
    def load_passwords_from_env(cls, data: t.Any) -> t.Any:
        try:
            assert isinstance(data, dict)
            accounts = data["accounts"]
            assert isinstance(accounts, list)
            assert all(
                isinstance(a, (dict, MatrixAccountConfig)) for a in accounts)
        except (AssertionError, KeyError):
            raise ValueError("invalid accounts format")
        # For each account, look for CLAWP_MATRIX_PASSWORD_N in the
        # environment.
        for i, account in enumerate(accounts):
            if isinstance(account, dict):

                def get_password():
                    return account.get("password")

                def set_password(password):
                    account["password"] = password

            else:
                assert isinstance(account, MatrixAccountConfig)

                def get_password():
                    return getattr(account, "password", None)

                def set_password(password):
                    account.password = password

            if get_password() is not None:
                # A password has been specified in the dict, prefer that over
                # an env variable.
                continue
            try:
                env_password = os.environ[f"CLAWP_MATRIX_PASSWORD_{i}"]
            except KeyError:
                # No password in environment, it must be in the dict already or
                # fail validation.
                continue
            set_password(env_password)
        return data


class ChannelsConfig(BaseSettings):
    matrix: MatrixConfig


class ApiConfig(BaseSettings):
    host: pyd.IPvAnyAddress
    port: int
    log_level: t.Literal["critical", "error", "warning", "info", "debug",
                         "trace"]


class ShellSshConfig(BaseSettings):
    host: str
    port: int
    username: str
    key_filename: pathlib.Path


class ShellConfig(BaseSettings):
    ssh: ShellSshConfig
    shell_binary: str
    # Value of the PATH variable in the shell.
    path: str


class ToolConfig(BaseSettings):
    shell: ShellConfig


class GatewayConfig(BaseSettings):
    files_base_dir: pathlib.Path
    """
    The base directory for all of the gateway's files.

    All persistent files the gateway needs will be stored below this path.
    """
    openrouter: OpenRouterConfig
    api: ApiConfig
    channels: ChannelsConfig
    tools: ToolConfig

    @pyd.computed_field
    @property
    def agents_base_dir(self) -> pathlib.Path:
        """The base directory for agent-specific data."""
        return self.files_base_dir / "agents"

    @pyd.model_validator(mode="after")
    def compute_matrix_store_dir(self) -> t.Self:
        self.channels.matrix.store_dir = self.files_base_dir / "matrix_nio"
        return self


class Config(BaseSettings):
    config_version: t.Literal[0]
    gateway: GatewayConfig
