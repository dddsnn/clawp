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
import whenever as we
import yarl

from . import base
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
    all event IDs at that timestamp (usually this should just be one, but they
    are use to disambiguate in case there are multiple events with the same
    timestamp).
    """
    last_event_time: base.Iso8601Millis
    last_event_ids: set[int]

    @staticmethod
    def min() -> GithubEventReadMarker:
        """Minimum value."""
        return GithubEventReadMarker(
            last_event_time=we.Instant.MIN, last_event_ids=set())


class GithubChannelState(base.BaseModel):
    """Persistent state for the Github channel."""
    read_markers: dict[yarl.URL, GithubEventReadMarker] = pyd.Field(
        default_factory=dict)
    """
    Read markers for the channel.

    Maps endpoint URL (including parameters like repo name or issue ID) to a
    read marker of the last event processed from that endpoint.
    """
    unread_messages: dict[str, list[msg.IncomingMessage]] = pyd.Field(
        default_factory=dict)
    """Unread messages, by chat_id."""


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
