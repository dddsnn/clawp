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
import uuid

import pydantic as pyd
import whenever as we
import yarl

from . import base, tool
from . import channel as chan
from . import message as msg


class WebUiChannelState(base.BaseModel):
    """Persistent state for the built-in web_ui channel."""

    read_offset: int = 0


class AgentChannelState(base.BaseModel):
    """Persistent state for the built-in agent channel."""

    read_offsets: dict[uuid.UUID, int] = pyd.Field(default_factory=dict)


class GithubEventReadMarker(base.BaseModel):
    """
    A read marker for events.

    The marker stores the timestamp of the last event read, as well as a set of
    all event node IDs at that timestamp (usually this should just be one, but
    they are use to disambiguate in case there are multiple events with the
    same timestamp).
    """

    last_event_time: base.Iso8601Millis
    last_event_ids: set[str]

    @staticmethod
    def min() -> GithubEventReadMarker:
        """Minimum value."""
        return GithubEventReadMarker(
            last_event_time=we.Instant.MIN, last_event_ids=set()
        )


class GithubChannelState(base.BaseModel):
    """Persistent state for the Github channel."""

    read_markers: dict[yarl.URL, GithubEventReadMarker] = pyd.Field(
        default_factory=dict
    )
    """
    Read markers for the channel.

    Maps endpoint URL (including parameters like repo name or issue ID) to a
    read marker of the last event processed from that endpoint.
    """
    unread_messages: dict[str, list[msg.IncomingMessage]] = pyd.Field(
        default_factory=dict
    )
    """Unread messages, by chat_id."""


class AgentState(base.BaseModel):
    """Mutable agent state."""

    claimed_channels: dict[chan.ChannelType, str] = pyd.Field(
        default_factory=dict
    )
    """
    Channels claimed by the agent.

    A mapping of channel type to channel ID.
    """
    active_chat: chan.ChatDescriptor
    web_ui_channel: WebUiChannelState
    agent_channel: AgentChannelState
    tools: tool.ToolSpecification


class GatewayState(base.BaseModel):
    """Mutable state of the entire gateway."""

    github_channels: dict[int, GithubChannelState] = pyd.Field(
        default_factory=dict
    )
    """State of Github accounts."""


class InfoMessageSpec[Type: t.Literal["init", "tutorial", "file_content"]](
    base.FrozenBaseModel, frozen=True
):
    """
    Base info message specification.

    An InfoMessageSpec specifies an information message that should be shown to
    the agent. It doesn't necessarily contain the message itself.
    """

    type: Type


class InfoMessageSpecInit(InfoMessageSpec[t.Literal["init"]], frozen=True):
    """
    Init info message specification.

    This is just a marker class for the fixed init message.
    """

    type: t.Literal["init"] = "init"


class InfoMessageSpecTutorial(
    InfoMessageSpec[t.Literal["tutorial"]], frozen=True
):
    """
    Tutorial message specification.

    This specifies the tutorial with the given topic.
    """

    type: t.Literal["tutorial"] = "tutorial"
    topic: str


class InfoMessageSpecFileContent(
    InfoMessageSpec[t.Literal["file_content"]], frozen=True
):
    """
    Specification for an info message displaying file content.

    The info message specified by this shows the content of a file in the
    agent's workspace.
    """

    type: t.Literal["file_content"] = "file_content"
    file_path: pathlib.Path


class InfoMessageSpecPersonalityFileContent(
    InfoMessageSpecFileContent, frozen=True
):
    """
    Specification for an info message showing a personality file.

    This specifies file content, with the addition that personalitiy files have
    an order in which they should be displayed.
    """

    index: int


class SessionState(base.BaseModel):
    """Persistent state pertaining to a session."""

    info_messages_shown: set[InfoMessageSpec[t.Any]] = pyd.Field(
        default_factory=set
    )
    """The set of info messages that has already been shown in the session."""
