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

import asyncio
import collections.abc as cl_abc
import dataclasses as dc
import logging
import typing as t

from .. import message as msg
from .. import model as mdl
from .. import util
from . import base, builtin, matrix


class ChannelError(Exception):
    """Raised when a channel is not available for any reason."""


class NoSuchChannelError(ChannelError):
    """Raised when a requested channel doesn't exist."""


class ChannelStateError(ChannelError):
    """Raised when a channel has already been acquired."""


class SendError(Exception):
    """Raised when a message could not be sent."""


class ChannelRouter(base.MessageSender):
    """
    A router for all of an agent's channels.

    Maintains the channels available to an agent, multiplexes incoming messages
    into a single stream, and routes outgoing messages to the appropriate
    channel based on the message's metadata.

    Only one channel of each type may be added. The built-in channels system
    and web_ui are added automatically if they don't exist.

    The asynchronous context manager takes control of the contexts of the
    channels, i.e. it expects them to not have been entered and instead
    controls their lifecycles.
    """
    @dc.dataclass
    class ChannelStatus:
        channel: base.Channel
        read_task: t.Optional[asyncio.Task] = None

    def __init__(self, channels: list[base.Channel]) -> None:
        self._logger = logging.getLogger(type(self).__name__)
        self._publisher = util.Publisher()
        self._is_running = False
        self._stati = {}
        for channel in channels:
            if channel.type in self._stati:
                raise ValueError(f"Channel {channel.type} specified twice.")
            self._stati[channel.type] = self.ChannelStatus(channel)
        if not any(isinstance(c, builtin.WebUiChannel) for c in channels):
            self._stati["web_ui"] = self.ChannelStatus(builtin.WebUiChannel())

    async def __aenter__(self) -> t.Self:
        self._logger.info(
            "Starting channel router with channels "
            f"{sorted(self._stati)}.")
        await self._publisher.__aenter__()
        for status in self._stati.values():
            await self._start_channel(status)
        self._is_running = True
        return self

    async def __aexit__(self, *args) -> bool:
        self._is_running = False
        await self._publisher.__aexit__(*args)
        for status in self._stati.values():
            await self._stop_channel(status)
        return False

    async def _start_channel(self, status):
        assert status.read_task is None
        await status.channel.__aenter__()
        status.read_task = asyncio.create_task(
            self._read_channel(status.channel))

    async def _read_channel(self, channel: base.Channel):
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

    async def _stop_channel(self, status):
        assert status.read_task is not None
        status.read_task.cancel()
        try:
            async with asyncio.timeout(60):
                await status.read_task
                await status.channel.__aexit__(None, None, None)
        except Exception:
            self._logger.exception(
                f"Error waiting for shutdown of {status.channel.type}.")

    async def add_channel(self, channel: base.Channel) -> None:
        """
        Add a new channel and start reading from it.

        Raises a ValueError if a channel of the type already exists. Raises a
        RuntimeError if the router isn't running.
        """
        if channel.type in self._stati:
            raise ValueError(f"channel {channel.type} already exists")
        if not self._is_running:
            raise RuntimeError("router isn't running, cant add channel")
        self._stati[channel.type] = self.ChannelStatus(channel)
        await self._start_channel(self._stati[channel.type])
        self._logger.info(f"Added and started channel {channel.type}.")

    async def remove_channel(self, channel_type: mdl.ChannelType) -> None:
        """
        Remove a channel.

        Raises a ValueError if the channel doesn't exist in this router.
        """
        try:
            status = self._stati[channel_type]
        except KeyError:
            raise ValueError(f"no channel of type {channel_type} exists")
        await self._stop_channel(status)
        del self._stati[channel_type]
        self._logger.info(f"Stopped and removed channel {channel_type}.")

    @property
    def channels(self) -> dict[str, base.Channel]:
        return {t: s.channel for t, s in self._stati.items()}

    @property
    def web_ui_channel(self) -> builtin.WebUiChannel:
        web_ui_channel = self.channels["web_ui"]
        assert isinstance(web_ui_channel, builtin.WebUiChannel)
        return web_ui_channel

    async def send(self, message: msg.AgentMessage) -> None:
        """
        Send a message.

        The message's metadata is checked to see which channel the message
        should be sent on.

        If the channel in the metadata doesn't exist, a ChannelUnavailableError
        is raised. If there is an error in sending the message, a SendError is
        raised.
        """
        try:
            channel_status = self._stati[message.metadata.chat.channel]
        except KeyError:
            raise NoSuchChannelError(
                f"no such channel {message.metadata.chat.channel}")
        try:
            await channel_status.channel.send(message)
        except Exception as e:
            raise SendError(f"error sending message: {e}") from e

    def incoming_messages(self) -> cl_abc.AsyncGenerator[mdl.ChatMessage]:
        """Iterate over incoming chat messages."""
        return self._publisher.subscribe()


@dc.dataclass
class PoolChannelStatus:
    channel: base.Channel
    config: mdl.Account
    status: t.Literal["available", "acquired"] = "available"


class ChannelPool:
    """
    A pool of all available channels.

    Creates channels from the channels config, and makes them available. Each
    channel can only be acquired once at a time.
    """
    def __init__(self, config: mdl.ChannelsConfig) -> None:
        self._channels = {"matrix": self._make_matrix_channels(config.matrix)}

    def _make_matrix_channels(
            self, config: mdl.MatrixConfig) -> dict[str, matrix.MatrixChannel]:
        channels = {}
        for account in config.accounts:
            channel_status = PoolChannelStatus(
                channel=matrix.MatrixChannel(
                    store_dir=config.store_dir, config=account),
                config=account)
            channels[account.id] = channel_status
        return channels

    def __iter__(self) -> cl_abc.Generator[PoolChannelStatus]:
        """
        Iterate all channel stati.

        Iterate over all channels managed by the pool for information purposes.
        Note that, while it is possible to get a hold of any channel like this,
        they are not acquired for the iteration in the sense of the pool. If
        the status says "acquired", they have been acquired previously and are
        in use. Actively using them will lead to concurrency issues.
        """
        for channels_of_type in self._channels.values():
            yield from channels_of_type.values()

    def acquire(
            self, channel_type: mdl.ChannelType,
            channel_id: str) -> PoolChannelStatus:
        """
        Acquire a specific channel.

        Raises NoSuchChannelError if the channel doesn't exist, or
        ChannelStateError if it has already been acquired.
        """
        try:
            status = self._channels[channel_type][channel_id]
        except KeyError:
            raise NoSuchChannelError(
                f"channel {channel_type}:{channel_id} doesn't exist")
        if status.status != "available":
            raise ChannelStateError("channel has already been acquired")
        status.status = "acquired"
        return status

    def release(self, channel: base.Channel) -> None:
        """
        Release an acquired channel so it can be aquired again.

        Raises NoSuchChannelError if the channel doesn't exist, or
        ChannelStateError if it has not been acquired.
        """
        try:
            status = self._channels[channel.type][channel.id]
        except KeyError:
            raise NoSuchChannelError(f"{channel} doesn't exist in the pool")
        if status.status != "acquired":
            raise ChannelStateError("channel has not been acquired")
        status.status = "available"
