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

import uuid

import pydantic as pyd

from . import base
from . import channel as chan


class WebUiChannelState(base.BaseModel):
    """Persistent state for the built-in web_ui channel."""
    read_offset: int = 0


class AgentChannelState(base.BaseModel):
    """Persistent state for the built-in agent channel."""
    read_offsets: dict[uuid.UUID, int] = pyd.Field(default_factory=dict)


class GithubChannelState(base.BaseModel):
    """Persistent state for the Github channel."""
    last_read_event_ids: dict[str, int] = pyd.Field(default_factory=dict)
    """
    Highest event ID that has already been shown to the recipient.
    """


class AgentState(base.BaseModel):
    """Mutable agent state."""
    claimed_channels: dict[chan.ChannelType, str] = (
        pyd.Field(default_factory=dict))
    """
    Channels claimed by the agent.

    A mapping of channel type to channel ID.
    """
    active_chat: chan.ChatDescriptor
    web_ui_channel: WebUiChannelState
    agent_channel: AgentChannelState


class GatewayState(base.BaseModel):
    """Mutable state of the entire gateway."""
    github_channels: dict[int, GithubChannelState] = pyd.Field(
        default_factory=dict)
    """State of Github accounts."""
