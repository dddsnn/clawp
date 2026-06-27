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

from .. import message as msg
from .. import model as mdl
from . import base

if t.TYPE_CHECKING:
    from .. import agent as agt


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

    async def get_chat_descriptor(self, chat_id: str) -> mdl.ChatDescriptor:
        if chat_id != "":
            raise base.ChatIdError("invalid chat_id (use empty string \"\")")
        return mdl.ChatDescriptor(channel=self.type, chat_id=chat_id)

    async def get_unread_messages(self, chat_id: str) -> list[mdl.ChatMessage]:
        if chat_id != "":
            raise base.ChatIdError("invalid chat_id (use empty string \"\")")
        return []

    async def send(self, message: msg.AgentMessage) -> None:
        self._logger.debug(f"Sending {message}: {await message.content}")

    async def add_incoming_user_message(
            self, time: we.Instant, content: str) -> None:
        """
        Add a user message.

        The message will appear has having arrived on the channel and will be
        delivered to the agent.
        """
        metadata = mdl.ChatMessageMetadata(
            time=time, chat=mdl.ChatDescriptor(channel=self.type, chat_id=""))
        message = mdl.ChatMessage(
            role="user", metadata=metadata, content=content)
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

    async def get_chat_descriptor(self, chat_id: str) -> mdl.ChatDescriptor:
        # Get the other agent to make sure the ID is in order and the agent
        # exists.
        self._get_agent(chat_id)
        return mdl.ChatDescriptor(channel=self.type, chat_id=chat_id)

    def _get_agent(self, chat_id: str) -> "agt.Agent":
        try:
            agent_id = uuid.UUID(chat_id)
        except ValueError:
            raise base.ChatIdError(f"{chat_id} is not a valid UUID")
        if agent_id == self._agent_id:
            raise base.ChatIdError("sender and recipient IDs are equal")
        try:
            return self._agent_repo.get_agent(agent_id)
        except KeyError:
            raise base.ChatIdError(f"no agent with ID {agent_id} exists")

    async def get_unread_messages(self, chat_id: str) -> list[mdl.ChatMessage]:
        # Get the other agent to make sure the ID is in order and the agent
        # exists.
        self._get_agent(chat_id)
        return []

    async def send(self, message: msg.AgentMessage) -> None:
        assert message.metadata.chat.channel == "agent"
        recipient = self._get_agent(message.metadata.chat.chat_id)
        try:
            recipient_channel = recipient.channels["agent"]
        except KeyError:
            raise RuntimeError(
                "recipient doesn't have an agent channel to send to")
        assert isinstance(recipient_channel, AgentChannel)
        await recipient_channel.add_incoming_agent_message(
            message, self._agent_id)

    async def add_incoming_agent_message(
            self, message: msg.AgentMessage, sender_id: uuid.UUID) -> None:
        """
        Add an incoming agent message.

        A user message will be constructed with the content of the given agent
        message. That message will appear has having arrived on the channel and
        will be delivered to the agent.
        """
        metadata = mdl.ChatMessageMetadata(
            time=await message.metadata.time.value,
            chat=mdl.ChatDescriptor(channel=self.type, chat_id=str(sender_id)))
        message = mdl.ChatMessage(
            role="user", metadata=metadata, content=await message.content)
        await self._publisher.append(message)
