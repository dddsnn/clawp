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

import re
import typing as t
import uuid

import pydantic as pyd

from . import base
from . import config as cfg

ChannelType = t.Literal["agent", "github", "matrix", "system", "web_ui"]


class BaseChatDescriptor[ChannelTypeLiteral](base.BaseModel):
    channel: ChannelTypeLiteral
    chat_id: str

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, BaseChatDescriptor):
            return False
        # channel/chat_id uniquely identify a chat, everything else is
        # optional.
        self_dict = self.model_dump(include={"channel", "chat_id"})
        other_dict = other.model_dump(include={"channel", "chat_id"})
        return self_dict == other_dict

    def __hash__(self) -> int:
        return hash((self.channel, self.chat_id))


class SystemChatDescriptor(BaseChatDescriptor[t.Literal["system"]]):
    channel: t.Literal["system"] = "system"


class WebUiChatDescriptor(BaseChatDescriptor[t.Literal["web_ui"]]):
    channel: t.Literal["web_ui"] = "web_ui"


class AgentChatDescriptor(BaseChatDescriptor[t.Literal["agent"]]):
    channel: t.Literal["agent"] = "agent"


class MatrixChatDescriptor(BaseChatDescriptor[t.Literal["matrix"]]):
    channel: t.Literal["matrix"] = "matrix"
    room_name: str | None


class GithubChatDescriptor(BaseChatDescriptor[t.Literal["github"]]):
    _chat_id_regex: t.ClassVar[re.Pattern[str]] = re.compile(
        r"""
        (?i)                   # Case-insensitive matching
        (?P<owner>             # Github username rules:
          [a-z0-9]             # Starts with alphanumeric
          (?:[a-z0-9]|-(?=[a-z0-9])){0,38} # Max 39 chars, single hyphens only
        )
        /                      # Literal forward slash
        (?P<repo>[a-z0-9_.-]+) # Repository name
        \#                     # Literal octothorpe
        (?P<number>\d+)        # The issue/PR number
    """,
        re.VERBOSE,
    )

    channel: t.Literal["github"] = "github"
    repo_full_name: str
    issue_type: t.Literal["issue", "pr"]
    issue_number: int
    issue_title: str
    issue_author: str

    @pyd.computed_field
    @property
    def repo_clone_url(self) -> str:
        return f"https://github.com/{self.repo_full_name}.git"

    @pyd.model_validator(mode="after")
    def validate_chat_id(self) -> t.Self:
        valid_chat_id = self.create_chat_id(
            self.repo_full_name, self.issue_number
        )
        if self.chat_id != valid_chat_id:
            raise ValueError(
                f"invalid chat_id format (must be {valid_chat_id})"
            )
        return self

    @staticmethod
    def create_chat_id(repo_full_name, issue_number):
        return f"{repo_full_name}#{issue_number}"

    @classmethod
    def parse_chat_id(cls, chat_id: str) -> tuple[str, int]:
        """Parse a chat_id into (repo_full_name, issue_number)."""
        match = cls._chat_id_regex.match(chat_id)
        if not match:
            raise ValueError(
                "chat ID doesn't match format (must be like "
                '"owner-name/repo-name#123"'
            )
        repo_full_name = f"{match.group('owner')}/{match.group('repo')}"
        issue_number = int(match.group("number"))
        return (repo_full_name, issue_number)


ChatDescriptor = (
    SystemChatDescriptor
    | WebUiChatDescriptor
    | AgentChatDescriptor
    | GithubChatDescriptor
    | MatrixChatDescriptor
)


class BaseChannelStatus[ChannelTypeLiteral: ChannelType](base.BaseModel):
    type: ChannelTypeLiteral
    available: bool


class SystemChannelStatus(BaseChannelStatus[t.Literal["system"]]):
    type: t.Literal["system"] = "system"


class WebUiChannelStatus(BaseChannelStatus[t.Literal["web_ui"]]):
    type: t.Literal["web_ui"] = "web_ui"


class AgentChannelStatus(BaseChannelStatus[t.Literal["agent"]]):
    type: t.Literal["agent"] = "agent"


class GithubChannelStatus(BaseChannelStatus[t.Literal["github"]]):
    type: t.Literal["github"] = "github"
    app_id: int
    installation_id: int
    login: str


class MatrixChannelStatus(BaseChannelStatus[t.Literal["matrix"]]):
    type: t.Literal["matrix"] = "matrix"
    username: str


ChannelStatus = t.Annotated[
    WebUiChannelStatus
    | AgentChannelStatus
    | GithubChannelStatus
    | MatrixChannelStatus,
    pyd.Field(discriminator="type"),
]


class ChannelInformation(base.BaseModel):
    type: ChannelType
    id: str | None
    config: cfg.ChannelAccountConfig
    status: ChannelStatus
    assigned_to_agent: uuid.UUID | None

    @pyd.model_validator(mode="after")
    def check_type_is_consistent(self) -> t.Self:
        if self.type != self.status.type:
            raise ValueError("type and status.type differ")
        return self
