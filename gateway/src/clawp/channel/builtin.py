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

import typing as t

import whenever as we

from .. import message as msg
from .. import model as mdl
from .. import util
from . import base


class SystemChannel(base.Channel):
    """
    System channel.

    This built-in channel is used for all messages that the system sends the
    agent. It's also a means for the agent to respond to the system directly
    whenever necessary.
    """
    def __init__(self) -> None:
        super().__init__("system")

    @property
    def id(self) -> None:
        return None

    @property
    async def status(self) -> mdl.SystemChannelStatus:
        return mdl.SystemChannelStatus(available=True)

    async def send(self, message: msg.AgentMessage) -> None:
        self._logger.info(
            f"Agent sent system message:\n{await message.content}")

    def response_channel(
        self, incoming_descriptor: mdl.SystemChannelDescriptor
    ) -> mdl.SystemChannelDescriptor:
        return incoming_descriptor

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
            channel=mdl.SystemChannelDescriptor())
        message = base.IncomingMessage(
            role=role, metadata=metadata, content=content,
            request_response=request_response)
        await self._publisher.append(message)


class WebUiChannel(base.Channel):
    """
    Web UI channel.

    This channel is used for the built-in web UI.
    """
    def __init__(self) -> None:
        super().__init__("web_ui")

    @property
    def id(self) -> None:
        return None

    @property
    async def status(self) -> mdl.WebUiChannelStatus:
        return mdl.WebUiChannelStatus(available=True)

    async def send(self, message: msg.AgentMessage) -> None:
        self._logger.debug(f"Sending {message}: {await message.content}")

    def response_channel(
        self, incoming_descriptor: mdl.WebUiChannelDescriptor
    ) -> mdl.WebUiChannelDescriptor:
        return incoming_descriptor

    async def add_incoming_user_message(
            self, time: we.Instant, content: str) -> None:
        """
        Add a user message.

        The message will appear has having arrived on the channel and will be
        delivered to the agent.
        """
        metadata = msg.IncomingMessageMetadata(
            time=util.ImmediateValue(time),
            channel=mdl.WebUiChannelDescriptor())
        message = base.IncomingMessage(
            role="user", metadata=metadata, content=content,
            request_response=True)
        await self._publisher.append(message)
