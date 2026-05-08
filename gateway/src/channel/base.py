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
import asyncio
import collections.abc as cl_abc
import dataclasses as dc
import logging
import typing as t

import whenever as we

import message as msg
import model as mdl
import util


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


class Channel(abc.ABC):
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

    @abc.abstractmethod
    async def send(self, message: msg.AgentMessage) -> None:
        """Send a message on this channel."""
        raise NotImplementedError

    def incoming_messages(self) -> cl_abc.AsyncGenerator[IncomingMessage]:
        """Iterate over incoming messages."""
        return self._publisher.subscribe()


class WebUiChannel(Channel):
    def __init__(self) -> None:
        super().__init__("web_ui")

    async def send(self, message: msg.AgentMessage) -> None:
        self._logger.info(f"Sending {message}: {await message.content}")

    async def add_incoming_user_message(
            self, time: we.Instant, content: str) -> None:
        metadata = msg.IncomingMessageMetadata(
            time=util.ImmediateValue(time),
            channel=util.ImmediateValue(mdl.WebUiChannelDescriptor()))
        message = IncomingMessage(
            role="user", metadata=metadata, content=content,
            request_response=True)
        await self._publisher.append(message)


class SystemChannel(Channel):
    def __init__(self) -> None:
        super().__init__("system")

    async def send(self, message: msg.AgentMessage) -> None:
        self._logger.info(f"Sending {message}: {await message.content}")

    async def add_incoming_message(
            self, role: t.Literal["developer", "tool", "system"], content: str,
            request_response: bool = False) -> None:
        metadata = msg.IncomingMessageMetadata(
            time=util.ImmediateValue(we.Instant.now()),
            channel=util.ImmediateValue(mdl.SystemChannelDescriptor()))
        message = IncomingMessage(
            role=role, metadata=metadata, content=content,
            request_response=request_response)
        await self._publisher.append(message)


class ChannelRepository:
    def __init__(self, channels: cl_abc.Iterable[Channel]) -> None:
        self._logger = logging.getLogger(type(self).__name__)
        self._publisher = util.Publisher()
        self._channels = {}
        for channel in channels:
            if channel.type in self._channels:
                raise ValueError(f"Channel {channel.type} specified twice.")
            self._channels[channel.type] = channel
        if not any(isinstance(c, SystemChannel) for c in channels):
            raise ValueError("missing system channel")
        if not any(isinstance(c, WebUiChannel) for c in channels):
            raise ValueError("missing web UI channel")
        self._channel_read_tasks = {}

    async def __aenter__(self) -> t.Self:
        await self._publisher.__aenter__()
        for channel in self._channels.values():
            await channel.__aenter__()
            self._channel_read_tasks[channel.type] = (
                asyncio.create_task(self._read_channel(channel)))
        return self

    async def __aexit__(self, *args) -> bool:
        await self._publisher.__aexit__(*args)
        for channel in self._channels.values():
            # TODO exc handling, timeouts++++++
            read_task = self._channel_read_tasks[channel.type]
            read_task.cancel()
            await read_task
            await channel.__aexit__(*args)
        return False

    async def _read_channel(self, channel: Channel):
        async for message in channel.incoming_messages():
            await self._publisher.append(message)

    @property
    def system_channel(self) -> SystemChannel:
        return self._channels["system"]

    async def send(self, message: msg.AgentMessage) -> None:
        self._logger.info(f"Sending {message}: {await message.content}")

    def incoming_messages(self) -> cl_abc.AsyncGenerator[IncomingMessage]:
        return self._publisher.subscribe()
