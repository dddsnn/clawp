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


class ChannelError(Exception):
    """Base exception for errors in channels."""


class ChatIdError(ChannelError, ValueError):
    """Raised when a chat ID is invalid in any way."""


class MessageSender(abc.ABC):
    @abc.abstractmethod
    def make_outgoing_start_metadata(
        self, chat: mdl.ChatDescriptor
    ) -> tuple[mdl.StartMessageMetadata, type[mdl.ChatMessageMetadata]]:
        """
        Create start metadata for outgoing chat messages.

        Returns a tuple of the start metadata and the model class for full
        metadata.

        Raises ChatIdError if the chat's ID is invalid.
        """
        raise NotImplementedError

    @abc.abstractmethod
    async def send(self, message: msg.AgentMessage) -> None:
        """Send a message."""
        raise NotImplementedError


@dc.dataclass
class IncomingMessage:
    chat: mdl.ChatDescriptor
    message: mdl.ChatMessage | mdl.SystemMessage


class Channel(MessageSender):
    """
    A communication channel.

    A channel is a way for the agent to communicate with the user or some other
    party. It can iterate over incoming chat messages (from the user/outside),
    and send messages back.

    A channel also maintains information on which messages have already been
    read. Every call to get_unread_messages() will return only those messages
    never returned by it before. Afterwards, they're marked as read.
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

        This identifies e.g. the account or username from which this channel
        sends. None for channels that have no identity associated with them.
        """
        raise NotImplementedError

    @property
    @abc.abstractmethod
    async def status(self) -> mdl.ChannelStatus:
        """Current status of the channel."""
        raise NotImplementedError

    @abc.abstractmethod
    async def get_chat_descriptor(self, chat_id: str) -> mdl.ChatDescriptor:
        """
        Get a full descriptor for the given chat ID.

        Raises ChatIdError if chat_id is invalid.
        """
        raise NotImplementedError

    def incoming_messages(self) -> cl_abc.AsyncGenerator[IncomingMessage]:
        """Iterate over incoming messages."""
        return self._publisher.subscribe()

    @abc.abstractmethod
    async def get_unread_messages(self, chat_id: str) -> list[IncomingMessage]:
        """
        Get messages that haven't yet been read.

        As a side effect, this also marks the messages as read (so that another
        call immediately following will return an empty list).

        Raises ChatIdError if the chat isn't valid in any way.
        """
        raise NotImplementedError
