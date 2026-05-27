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
import collections.abc as cl_abc
import dataclasses as dc
import logging
import typing as t

from .. import message as msg
from .. import model as mdl
from .. import util


@dc.dataclass
class IncomingMessage:
    """
    An incoming message.

    This represents a message that's just arrived and doesn't exist within an
    agent's session yet. It is missing the metadata related to the agent.
    """
    role: msg.MessageRole
    metadata: msg.IncomingMessageMetadata
    content: str
    request_response: bool
    """Whether a response to this message should be requested."""
    def __post_init__(self):
        if self.request_response and self.role not in ["system", "user"]:
            raise ValueError(
                "only system and user messages may request a response")


class MessageSender(abc.ABC):
    @abc.abstractmethod
    async def send(self, message: msg.AgentMessage) -> None:
        """Send a message."""
        raise NotImplementedError

    @abc.abstractmethod
    def response_channel(
        self, incoming_descriptor: mdl.IncomingChannelDescriptor
    ) -> mdl.OutgoingChannelDescriptor:
        """
        Create a channel descriptor for a response.

        Takes an incoming channel descriptor and returns an outgoing channel
        descriptor that will lead to a message being sent to whoever sent a
        message with the incoming descriptor."""
        raise NotImplementedError


class MessageReceiver(abc.ABC):
    def incoming_messages(self) -> cl_abc.AsyncGenerator[IncomingMessage]:
        """Iterate over incoming messages."""
        raise NotImplementedError


class Channel(MessageSender, MessageReceiver):
    """
    A communication channel.

    A channel is a way for the agent to communicate with the user. It can
    iterate over incoming messages (from the user/outside), and send messages
    back.
    """
    def __init__(self, channel_type: mdl.ChannelType) -> None:
        self._logger = logging.getLogger(type(self).__name__)
        self._type: mdl.ChannelType = channel_type
        self._publisher = util.Publisher()

    async def __aenter__(self) -> t.Self:
        await self._publisher.__aenter__()
        return self

    async def __aexit__(self, *args) -> bool:
        await self._publisher.__aexit__(*args)
        return False

    @property
    def type(self) -> mdl.ChannelType:
        return self._type

    @property
    @abc.abstractmethod
    def id(self) -> t.Optional[str]:
        """
        ID for the particular instance of the channel.

        This identifies e.g. the account or username from this this channel
        sends. None for channels that have no identity associated with them.
        """
        raise NotImplementedError

    @property
    @abc.abstractmethod
    async def status(self) -> mdl.ChannelStatus:
        """Current status of the channel."""
        raise NotImplementedError

    def incoming_messages(self) -> cl_abc.AsyncGenerator[IncomingMessage]:
        """Iterate over incoming messages."""
        return self._publisher.subscribe()
