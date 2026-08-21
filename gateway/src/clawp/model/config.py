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
import whenever as we


class BaseSecretValue[TypeLiteral](pyd.BaseModel, metaclass=abc.ABCMeta):
    type: TypeLiteral

    @property
    @abc.abstractmethod
    def value(self) -> str:
        raise NotImplementedError


class EnvironmentSecretValue(BaseSecretValue[t.Literal["environment"]]):
    type: t.Literal["environment"] = "environment"
    variable_name: str
    _value: str | None = pyd.PrivateAttr(default=None)

    @pyd.model_validator(mode="after")
    def resolve_value(self) -> t.Self:
        try:
            self._value = os.environ[self.variable_name]
        except KeyError:
            raise ValueError(
                f"environment variable {self.variable_name} doesn't exist"
            )
        return self

    @property
    def value(self) -> str:
        assert self._value is not None
        return self._value


class FileSecretValue(BaseSecretValue[t.Literal["file"]]):
    type: t.Literal["file"] = "file"
    path: pathlib.Path
    _value: str | None = pyd.PrivateAttr(default=None)

    @pyd.model_validator(mode="after")
    def resolve_value(self) -> t.Self:
        try:
            with self.path.open() as f:
                self._value = f.read()
        except Exception as e:
            raise ValueError(f"error reading secret file {self.path}") from e
        return self

    @property
    def value(self) -> str:
        assert self._value is not None
        return self._value


SecretValue = EnvironmentSecretValue | FileSecretValue


class Account[TypeLiteral](pyd.BaseModel, metaclass=abc.ABCMeta):
    type: TypeLiteral

    @pyd.computed_field
    @property
    @abc.abstractmethod
    def id(self) -> str:
        raise NotImplementedError


class ModelConfig(pyd.BaseModel):
    name: str
    doom_loop_max_requests: int
    message_send_timeout: we.TimeDelta
    request_timeout: we.TimeDelta

    @pyd.model_validator(mode="after")
    def check_timeout_proportions(self) -> t.Self:
        if self.message_send_timeout >= self.request_timeout:
            raise ValueError(
                "request timeout must be greater than message send timeout"
            )
        return self


class OpenRouterConfig(pyd.BaseModel):
    api_key: SecretValue
    model: ModelConfig


class GithubAccountConfig(Account[t.Literal["github"]]):
    type: t.Literal["github"] = "github"
    app_id: int
    installation_id: int
    private_key: SecretValue
    organization: str
    agent_email: pyd.EmailStr
    poll_interval: we.TimeDelta = we.TimeDelta(minutes=1)
    """
    Interval with which the API is polled.

    This can be overridden by Github's X-Poll-Interval header.
    """

    @property
    def id(self) -> str:
        return f"{self.app_id}:{self.installation_id}"


class GithubConfig(pyd.BaseModel):
    accounts: list[GithubAccountConfig]


class MatrixAccountConfig(Account[t.Literal["matrix"]]):
    type: t.Literal["matrix"] = "matrix"
    homeserver: str
    username: str
    password: SecretValue
    device_id: str

    @property
    def id(self) -> str:
        return self.username


class MatrixConfig(pyd.BaseModel):
    # Default this to None, it will be set relative to the gateway's store_dir.
    store_dir: pathlib.Path = pyd.Field(default=None, validate_default=False)  # pyright: ignore[reportAssignmentType]
    accounts: list[MatrixAccountConfig]


ChannelAccountConfig = t.Annotated[
    GithubAccountConfig | MatrixAccountConfig,
    pyd.Field(discriminator="type"),
]


class ChannelsConfig(pyd.BaseModel):
    github: GithubConfig
    matrix: MatrixConfig


class ApiConfig(pyd.BaseModel):
    host: pyd.IPvAnyAddress
    port: int
    log_level: t.Literal[
        "critical", "error", "warning", "info", "debug", "trace"
    ]


class ShellSshConfig(pyd.BaseModel):
    host: str
    port: int
    username: str
    key_filename: pathlib.Path


class ShellConfig(pyd.BaseModel):
    ssh: ShellSshConfig
    path: str
    """Value of the PATH variable in the shell."""


class ToolConfig(pyd.BaseModel):
    client_timeout: we.TimeDelta
    shell: ShellConfig


class GatewayConfig(pyd.BaseModel):
    files_base_dir: pathlib.Path
    """
    The base directory for all of the gateway's files.

    All persistent files the gateway needs will be stored below this path.
    """
    umask: int
    """
    The umask to set for the gateway process.

    This can be used as part of a permission strategy to prevent agents having
    access to gateway-internal files using their shell tool.
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


class Config(pyd.BaseModel):
    config_version: t.Literal[0]
    gateway: GatewayConfig
