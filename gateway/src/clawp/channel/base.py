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
import logging

from .. import agent as agt
from .. import file, util
from .. import message as msg
from .. import model as mdl


class ChannelError(Exception):
    """Base exception for errors in channels."""


class ChatIdError(ChannelError, ValueError):
    """Raised when a chat ID is invalid in any way."""


class MessageSender(metaclass=abc.ABCMeta):
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


class Channel(MessageSender, file.InfoProvider, metaclass=abc.ABCMeta):
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

    async def start(self, agent: agt.Agent) -> None:
        self._agent = agent
        await self._publisher.__aenter__()

    async def stop(self) -> None:
        await self._publisher.__aexit__(None, None, None)
        del self._agent

    @property
    def type(self) -> mdl.ChannelType:
        return self._type

    @property
    @abc.abstractmethod
    def id(self) -> str | None:
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

    async def get_extra_shell_env(self) -> dict[str, str]:
        """
        Get extra environment variables that should be set in the shell tool.

        This can be used to inject environment variables that make tools
        related to the channel available in the shell tool.
        """
        return {}

    @abc.abstractmethod
    async def get_chat_descriptor(self, chat_id: str) -> mdl.ChatDescriptor:
        """
        Get a full descriptor for the given chat ID.

        Raises ChatIdError if chat_id is invalid.
        """
        raise NotImplementedError

    def incoming_messages(self) -> cl_abc.AsyncGenerator[mdl.IncomingMessage]:
        """Iterate over incoming messages."""
        return self._publisher.subscribe()

    @abc.abstractmethod
    async def num_unread_messages(self, chat_id: str) -> int:
        """
        Get the number of available unread messages.

        The next call to get_unread_messages() will return at least this many
        messages.

        Raises ChatIdError if the chat isn't valid in any way.
        """
        raise NotImplementedError

    @abc.abstractmethod
    async def get_unread_messages(
        self, chat_id: str
    ) -> list[mdl.IncomingMessage]:
        """
        Get messages that haven't yet been read.

        As a side effect, this also marks the messages as read (so that another
        call immediately following will return an empty list).

        Raises ChatIdError if the chat isn't valid in any way.
        """
        raise NotImplementedError
