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

import whenever as we

import message as msg
import model as mdl
import util


@dc.dataclass
class ChannelMessage:
    role: msg.MessageRole
    metadata: msg.ReceivedMessageMetadata
    content: str
    request_response: bool


# TODO distinction raw message vs message: raw message without metadata. gets
# returned by subscribe(), and filled in with metadata by the agent+++++++
class Channel(abc.ABC):
    """
    TODO+++++++++
    """
    def __init__(
        self,
        channel_type: mdl.ChannelType,
        # on_message: cl_abc.Callable[[ChannelMessage], cl_abc.Awaitable[None]]
    ) -> None:
        self._logger = logging.getLogger(type(self).__name__)
        self._channel_type: mdl.ChannelType = channel_type
        # self._on_message = on_message

    @property
    def channel_type(self) -> mdl.ChannelType:
        return self._channel_type

    @abc.abstractmethod
    async def send(self, message: msg.Message) -> None:
        """
        TODO+++++++++++++
        """
        raise NotImplementedError


class WebUiChannel(Channel):
    def __init__(self) -> None:
        super().__init__("web_ui")

    async def send(self, message: msg.Message) -> None:
        # TODO+++++++++++++++
        self._logger.info(f"sending {message}: {await message.content}")

    def all_messages(self) -> cl_abc.AsyncGenerator[ChannelMessage]:
        """
        TODO+++++++++++++
        """
        return self._publisher.subscribe()


class SystemChannel(Channel):
    def __init__(self) -> None:
        super().__init__("system")

    async def send(self, message: msg.Message) -> None:
        # TODO+++++++++++++++
        self._logger.info(f"sending {message}: {await message.content}")


class ChannelRepository:
    """
    TODO+++++++++
    """
    def __init__(self, channels: cl_abc.Iterable[Channel]) -> None:
        self._logger = logging.getLogger(type(self).__name__)
        self._publisher = util.Publisher()
        # REFACTOR++++++++++++++
        # TODO validate
        self._channels = {}
        for channel in channels:
            if channel.channel_type in self._channels:
                raise ValueError(
                    f"Channel {channel.channel_type} specified twice.")
            self._channels[channel.channel_type] = channel
        if not any(isinstance(c, SystemChannel) for c in channels):
            raise ValueError("missing system channel")
        if not any(isinstance(c, WebUiChannel) for c in channels):
            raise ValueError("missing web UI channel")

    async def __aenter__(self) -> t.Self:
        await self._publisher.__aenter__()
        return self

    async def __aexit__(self, *args) -> bool:
        await self._publisher.__aexit__(*args)
        return False

    async def send(self, message: msg.Message) -> None:
        """
        TODO+++++++++++++
        """
        self._logger.info(f"sending {message}: {await message.content}")
        # TODO++++++++

    def incoming_messages(self) -> cl_abc.AsyncGenerator[ChannelMessage]:
        """
        TODO+++++++++++++
        """
        return self._publisher.subscribe()

    async def _add_incoming_message(
            self, channel: mdl.ChannelDescriptor, role: msg.MessageRole,
            content: str, request_response: bool = False) -> None:
        metadata = msg.ReceivedMessageMetadata(
            time=util.ImmediateValue(we.Instant.now()),
            channel=util.ImmediateValue(channel))
        message = ChannelMessage(
            role=role, metadata=metadata, content=content,
            request_response=False)
        await self._publisher.append(message)

    async def add_incoming_system_message(
            self, content: str, request_response: bool = False) -> None:
        await self._add_incoming_message(
            mdl.SystemChannelDescriptor(), "system", content, request_response)

    async def add_incoming_developer_message(
            self, content: str, request_response: bool = False) -> None:
        await self._add_incoming_message(
            mdl.SystemChannelDescriptor(), "developer", content,
            request_response)
