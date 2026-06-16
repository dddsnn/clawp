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
import uuid

import whenever as we

from .. import file, util
from .. import message as msg
from .. import model as mdl
from . import base

if t.TYPE_CHECKING:
    from .. import agent as agt


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
    ) -> mdl.OutgoingChannelDescriptor:
        if incoming_descriptor.override_outgoing_channel:
            return incoming_descriptor.override_outgoing_channel
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
        if role in ["developer", "system"] and request_response:
            # This is a system message prompting an agent response. Add a
            # reminder for the agent that its output will go on the system
            # channel.
            content += await file.render_message_template(
                "fragments/channel_reminder.md")
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


class AgentChannel(base.Channel):
    """
    Agent channel.

    This channel can be used by Clawp agents to directly communicate with other
    agents within the system.
    """
    def __init__(
            self, agent_id: uuid.UUID,
            agent_repo: "agt.AgentRepository") -> None:
        super().__init__("agent")
        self._agent_id = agent_id
        self._agent_repo = agent_repo

    @property
    def id(self) -> str:
        return str(self._agent_id)

    @property
    async def status(self) -> mdl.AgentChannelStatus:
        return mdl.AgentChannelStatus(available=True)

    async def send(self, message: msg.AgentMessage) -> None:
        channel = message.metadata.channel
        if not isinstance(channel, mdl.AgentOutgoingChannelDescriptor):
            raise ValueError(
                "cannot send to another agent without agent channel "
                f"descriptor (got {channel})")
        if channel.recipient_id == self._agent_id:
            raise ValueError("sender and recipient IDs are equal")
        try:
            recipient = self._agent_repo.get_agent(
                message.metadata.channel.recipient_id)
        except KeyError:
            raise ValueError(
                f"no agent with ID {message.metadata.channel.recipient_id} "
                "exists")
        try:
            recipient_channel = recipient.channels["agent"]
        except KeyError:
            raise ValueError(
                "recipient doesn't have an agent channel to send to")
        assert isinstance(recipient_channel, AgentChannel)
        await recipient_channel.add_incoming_agent_message(
            message, self._agent_id)

    def response_channel(
        self, incoming_descriptor: mdl.AgentIncomingChannelDescriptor
    ) -> mdl.AgentOutgoingChannelDescriptor:
        return mdl.AgentOutgoingChannelDescriptor(
            recipient_id=incoming_descriptor.sender_id)

    async def add_incoming_agent_message(
            self, message: msg.AgentMessage, sender_id: uuid.UUID) -> None:
        """
        Add an incoming agent message.

        A user message will be constructed with the content of the given agent
        message. That message will appear has having arrived on the channel and
        will be delivered to the agent. The agent needs to examine the metadata
        to see that the message comes from another agent.

        Following that message, a system message is added reminding the agent
        that they have the option to not respond by leaving their message
        content empty.
        """
        metadata = msg.IncomingMessageMetadata(
            time=message.metadata.time,
            channel=mdl.AgentIncomingChannelDescriptor(sender_id=sender_id))
        message = base.IncomingMessage(
            role="user", metadata=metadata, content=await message.content,
            request_response=True)
        await self._publisher.append(message)
