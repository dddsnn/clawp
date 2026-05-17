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


class MessageSender(abc.ABC):
    @abc.abstractmethod
    async def send(self, message: msg.AgentMessage) -> None:
        """Send a message."""
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
    async def channel_available_message(self) -> str:
        """
        Message for the agent informing them that this channel is available.

        This is the text of a system information message that states that this
        channel is available, what it's good for, and how to use it.
        """
        raise NotImplementedError

    def incoming_messages(self) -> cl_abc.AsyncGenerator[IncomingMessage]:
        """Iterate over incoming messages."""
        return self._publisher.subscribe()


class SystemChannel(Channel):
    """
    System channel.

    This built-in channel is used for all messages that the system sends the
    agent. It's also a means for the agent to respond to the system directly
    whenever necessary.
    """
    def __init__(self) -> None:
        super().__init__("system")

    @property
    async def channel_available_message(self) -> str:
        return await util.render_message_template(
            "channel_status", "system_available.md")

    async def send(self, message: msg.AgentMessage) -> None:
        self._logger.info(
            f"Agent sent system message:\n{await message.content}")

    async def add_incoming_message(
            self, role: t.Literal["developer", "tool", "system"], content: str,
            request_response: bool = False) -> None:
        """
        Add an incoming message.

        The message will appear has having arrived on the channel and will be
        delivered to the agent.
        """
        metadata = msg.IncomingMessageMetadata(
            time=util.ImmediateValue(we.Instant.now()),
            channel=util.ImmediateValue(mdl.SystemChannelDescriptor()))
        message = IncomingMessage(
            role=role, metadata=metadata, content=content,
            request_response=request_response)
        await self._publisher.append(message)


class WebUiChannel(Channel):
    """
    Web UI channel.

    This channel is used for the built-in web UI.
    """
    def __init__(self) -> None:
        super().__init__("web_ui")

    @property
    async def channel_available_message(self) -> str:
        return await util.render_message_template(
            "channel_status", "web_ui_available.md")

    async def send(self, message: msg.AgentMessage) -> None:
        self._logger.debug(f"Sending {message}: {await message.content}")

    async def add_incoming_user_message(
            self, time: we.Instant, content: str) -> None:
        """
        Add a user message.

        The message will appear has having arrived on the channel and will be
        delivered to the agent.
        """
        metadata = msg.IncomingMessageMetadata(
            time=util.ImmediateValue(time),
            channel=util.ImmediateValue(mdl.WebUiChannelDescriptor()))
        message = IncomingMessage(
            role="user", metadata=metadata, content=content,
            request_response=True)
        await self._publisher.append(message)


class ChannelRepository:
    """
    A repository of all of an agent's channels

    Maintains the channels available to an agent, multiplexes incoming messages
    into a single stream, and routes outgoing messages to the appropriate
    channel based on the message's metadata.

    The built-in channels system and web_ui must always exist.

    The asynchronous context manager takes control of the contexts of the
    channels, i.e. it expects them to not have been entered and instead
    controls their lifecycles.
    """
    @dc.dataclass
    class ChannelStatus:
        channel: Channel
        read_task: t.Optional[asyncio.Task] = None

    def __init__(self, channels: cl_abc.Iterable[Channel]) -> None:
        self._logger = logging.getLogger(type(self).__name__)
        self._publisher = util.Publisher()
        self._stati = {}
        for channel in channels:
            if channel.type in self._stati:
                raise ValueError(f"Channel {channel.type} specified twice.")
            self._stati[channel.type] = self.ChannelStatus(channel)
        if not any(isinstance(c, SystemChannel) for c in channels):
            raise ValueError("missing system channel")
        if not any(isinstance(c, WebUiChannel) for c in channels):
            raise ValueError("missing web UI channel")

    async def __aenter__(self) -> t.Self:
        self._logger.info(
            "Starting channel repository with channels "
            f"{sorted(self._stati)}.")
        await self._publisher.__aenter__()
        for status in self._stati.values():
            await status.channel.__aenter__()
            status.read_task = asyncio.create_task(
                self._read_channel(status.channel))
        return self

    async def __aexit__(self, *args) -> bool:
        await self._publisher.__aexit__(*args)
        for status in self._stati.values():
            status.read_task.cancel()
            try:
                async with asyncio.timeout(60):
                    await status.read_task
                    await status.channel.__aexit__(*args)
            except Exception:
                self._logger.exception(
                    f"Error waiting for shutdown of {status.channel.type}.")
        return False

    async def _read_channel(self, channel: Channel):
        publish_task = None
        try:
            async for message in channel.incoming_messages():
                publish_task = asyncio.create_task(
                    self._publisher.append(message))
                await asyncio.shield(publish_task)
        except asyncio.CancelledError:
            if not publish_task:
                return
            try:
                async with asyncio.timeout(60):
                    await publish_task
            except Exception:
                self._logger.exception("Error waiting for final publish.")
                publish_task.cancel()

    @property
    def channels(self) -> dict[str, Channel]:
        return {t: s.channel for t, s in self._stati.items()}

    @property
    def system_channel(self) -> SystemChannel:
        system_channel = self.channels["system"]
        assert isinstance(system_channel, SystemChannel)
        return system_channel

    async def send(self, message: msg.AgentMessage) -> None:
        """
        Send a message.

        The message's metadata is checked to see which channel the message
        should be sent on. If the channel doesn't exist, a KeyError is raised.
        """
        channel_descriptor = await message.metadata.channel.value
        try:
            channel_status = self._stati[channel_descriptor.type]
        except KeyError:
            raise ValueError(f"no such channel {channel_descriptor.type}")
        self._logger.debug(f"Sending {message}: {await message.content}")
        await channel_status.channel.send(message)

    def incoming_messages(self) -> cl_abc.AsyncGenerator[IncomingMessage]:
        """Iterate over incoming messages."""
        return self._publisher.subscribe()
