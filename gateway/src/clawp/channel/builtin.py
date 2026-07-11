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
        self._messages = []
        self._read_offset = 0

    @property
    def id(self) -> None:
        return None

    @property
    async def status(self) -> mdl.WebUiChannelStatus:
        return mdl.WebUiChannelStatus(available=True)

    async def get_chat_descriptor(
            self, chat_id: str) -> mdl.BasicChatDescriptor:
        if chat_id != "":
            raise base.ChatIdError("invalid chat_id (use empty string \"\")")
        return mdl.BasicChatDescriptor(channel=self.type, chat_id=chat_id)

    async def get_unread_messages(self, chat_id: str) -> list[mdl.ChatMessage]:
        if chat_id != "":
            raise base.ChatIdError("invalid chat_id (use empty string \"\")")
        unread_messages = self._messages[self._read_offset:]
        self._read_offset = len(self._messages)
        return unread_messages

    def make_outgoing_start_metadata(
        self, chat: mdl.ChatDescriptor
    ) -> tuple[mdl.BasicStartMessageMetadata,
               t.Literal[mdl.BasicChatMessageMetadata]]:
        if chat.channel != "web_ui":
            raise ValueError(f"got descriptor for {chat.channel}")
        return (
            mdl.BasicStartMessageMetadata(chat=chat),
            mdl.BasicChatMessageMetadata)

    async def send(self, message: msg.AgentMessage) -> None:
        chat_message = await message.model
        if len(self._messages) != self._read_offset:
            raise RuntimeError("can't send if there are unread messages")
        self._messages.append(chat_message)
        self._read_offset += 1

    async def add_incoming_user_message(
            self, time: we.Instant, content: str) -> None:
        """
        Add a user message.

        The message will appear has having arrived on the channel and will be
        delivered to the agent.
        """
        metadata = mdl.BasicChatMessageMetadata(
            time=time,
            chat=mdl.BasicChatDescriptor(channel=self.type, chat_id=""))
        message = mdl.ChatMessage(
            role="user", metadata=metadata, content=content)
        self._messages.append(message)
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
        self._messages = {}
        self._read_offsets = {}

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
        return mdl.BasicChatDescriptor(channel=self.type, chat_id=chat_id)

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
        messages = self._messages.setdefault(chat_id, [])
        read_offset = self._read_offsets.setdefault(chat_id, 0)
        unread_messages = messages[read_offset:]
        self._read_offsets[chat_id] = len(messages)
        return unread_messages

    def make_outgoing_start_metadata(
        self, chat: mdl.ChatDescriptor
    ) -> tuple[mdl.BasicStartMessageMetadata,
               t.Literal[mdl.BasicChatMessageMetadata]]:
        if chat.channel != "agent":
            raise ValueError(f"got descriptor for {chat.channel}")
        return (
            mdl.BasicStartMessageMetadata(chat=chat),
            mdl.BasicChatMessageMetadata)

    async def send(self, message: msg.AgentMessage) -> None:
        assert message.metadata.chat.channel == "agent"
        recipient = self._get_agent(message.metadata.chat.chat_id)
        try:
            recipient_channel = recipient.channels["agent"]
        except KeyError:
            raise RuntimeError(
                "recipient doesn't have an agent channel to send to")
        assert isinstance(recipient_channel, AgentChannel)
        chat_message = await message.model
        messages = self._messages.setdefault(message.metadata.chat.chat_id, [])
        read_offset = self._read_offsets.setdefault(
            message.metadata.chat.chat_id, 0)
        if len(messages) != read_offset:
            raise RuntimeError("can't send if there are unread messages")
        messages.append(chat_message)
        self._read_offsets[message.metadata.chat.chat_id] += 1
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
        metadata = mdl.BasicChatMessageMetadata(
            time=await message.metadata.time.value,
            chat=mdl.BasicChatDescriptor(
                channel=self.type, chat_id=str(sender_id)))
        message = mdl.ChatMessage(
            role="user", metadata=metadata, content=await message.content)
        self._messages.setdefault(message.metadata.chat.chat_id,
                                  []).append(message)
        await self._publisher.append(message)
